"""
File decryption helpers for AppX bot — v1.0

Handles:
  • PDF password decryption (pikepdf)
  • AES-128 video decryption (for AppX custom-encrypted MKV/MP4 files)
  • Detection of whether a downloaded file is actually encrypted

AppX V2 video encryption:
  AppX uses a simple XOR or AES-128-CBC scheme on some content. Others are
  standard HLS AES-128 streams where yt-dlp handles decryption automatically.
  For files downloaded as raw bytes that turn out to be encrypted containers,
  we attempt decryption using keys from the AppX API or from user-configured
  DRM keys.
"""

import logging
import os
import struct
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Magic bytes for common video formats ─────────────────────────────────────
_MAGIC = {
    b"\x00\x00\x00\x18ftyp": "mp4",     # MP4 / M4V
    b"\x00\x00\x00\x1cftyp": "mp4",
    b"\x00\x00\x00\x20ftyp": "mp4",
    b"\x1a\x45\xdf\xa3":      "mkv",     # MKV / WebM
    b"OggS":                   "ogg",
    b"\xff\xfb":               "mp3",
    b"\xff\xf3":               "mp3",
    b"%PDF":                   "pdf",
}


def detect_format(path: str) -> Optional[str]:
    """
    Read first 32 bytes and detect file format by magic bytes.
    Returns format string ("mp4", "mkv", "pdf", etc.) or None if unknown.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(32)
        for magic, fmt in _MAGIC.items():
            if header[: len(magic)] == magic:
                return fmt
        # Check for ftyp box at offset 4 (common MP4 variant)
        if len(header) >= 8 and header[4:8] == b"ftyp":
            return "mp4"
        return None
    except Exception as e:
        logger.debug("detect_format(%s): %s", path, e)
        return None


def is_likely_encrypted(path: str) -> bool:
    """
    Heuristic: if we can't detect a known format, the file is probably
    encrypted (or zero-length / corrupt).
    """
    if not os.path.exists(path) or os.path.getsize(path) < 16:
        return True
    return detect_format(path) is None


# ── PDF decryption ────────────────────────────────────────────────────────────
_PDF_PASSWORDS = [
    "", "appx", "appxco", "appx123", "123456", "password",
    "appxlearn", "learn", "course", "admin", "student",
    "pdf", "protected", "secure", "locked",
]


def try_decrypt_pdf(src: str, dst: str) -> bool:
    """
    Try a list of common AppX PDF passwords.
    Returns True if decryption succeeded and dst was written.
    """
    try:
        import pikepdf
    except ImportError:
        logger.debug("pikepdf not installed — skipping PDF decrypt")
        return False

    for pwd in _PDF_PASSWORDS:
        try:
            with pikepdf.open(src, password=pwd) as pdf:
                pdf.save(dst)
            logger.info("PDF decrypted with password=%r", pwd)
            return True
        except pikepdf.PasswordError:
            continue
        except Exception as e:
            logger.debug("pikepdf open(%r): %s", pwd, e)
            break
    return False


# ── AES-128 video decryption ──────────────────────────────────────────────────
def try_decrypt_aes128(
    src:  str,
    dst:  str,
    key:  bytes,
    iv:   bytes = b"\x00" * 16,
) -> bool:
    """
    Decrypt a file encrypted with AES-128-CBC.

    AppX sometimes uses a fixed IV of all-zeros; pass the actual IV if known.
    Returns True if decryption succeeded.
    """
    try:
        from Crypto.Cipher import AES  # pycryptodome
    except ImportError:
        try:
            from Cryptodome.Cipher import AES  # pycryptodomex
        except ImportError:
            logger.debug("pycryptodome not installed — skipping AES decrypt")
            return False

    try:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        chunk_size = 64 * 1024  # 64 KB chunks

        with open(src, "rb") as fh_in, open(dst, "wb") as fh_out:
            while True:
                chunk = fh_in.read(chunk_size)
                if not chunk:
                    break
                # Pad to block boundary if needed
                if len(chunk) % 16:
                    chunk += b"\x00" * (16 - len(chunk) % 16)
                fh_out.write(cipher.decrypt(chunk))

        logger.info("AES-128 decrypt succeeded: %s → %s", src, dst)
        return True
    except Exception as e:
        logger.warning("AES-128 decrypt failed: %s", e)
        if os.path.exists(dst):
            os.remove(dst)
        return False


def try_decrypt_with_drm_keys(
    src:      str,
    dst:      str,
    drm_keys: Dict[str, str],
    content_id: str = "",
) -> bool:
    """
    Try to decrypt using DRM key map {kid_hex: key_hex}.
    Looks for a key matching content_id, then tries all keys in order.
    """
    if not drm_keys:
        return False

    candidates = []
    if content_id:
        # Look for a matching content ID in the keys
        for kid, key_hex in drm_keys.items():
            if content_id.lower() in kid.lower() or kid.lower() in content_id.lower():
                candidates.append((kid, key_hex))

    # Add remaining keys as fallback
    for kid, key_hex in drm_keys.items():
        if (kid, drm_keys[kid]) not in candidates:
            candidates.append((kid, drm_keys[kid]))

    for kid, key_hex in candidates:
        try:
            key = bytes.fromhex(key_hex.replace(":", ""))
            if len(key) not in (16, 24, 32):
                continue
            logger.info("Trying DRM key for kid=%s", kid)
            tmp = dst + ".tmp"
            if try_decrypt_aes128(src, tmp, key):
                if detect_format(tmp) is not None:
                    os.replace(tmp, dst)
                    logger.info("DRM key %s ✅ decrypted %s", kid, src)
                    return True
                os.remove(tmp)
        except Exception as e:
            logger.debug("DRM key %s: %s", kid, e)

    return False


# ── ffmpeg post-processing ────────────────────────────────────────────────────
async def try_remux_mp4(src: str, dst: str) -> bool:
    """
    Attempt to remux a video file to a clean MP4 container using ffmpeg.
    Useful when yt-dlp downloads an encrypted stream but doesn't produce
    a valid mp4 (e.g. raw TS stream).

    Returns True if remux succeeded.
    """
    import asyncio
    import shutil

    if not shutil.which("ffmpeg"):
        logger.debug("ffmpeg not found — skipping remux")
        return False

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src, "-c", "copy", dst,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            logger.info("ffmpeg remux OK: %s → %s", src, dst)
            return True
        logger.warning("ffmpeg remux failed (rc=%d): %s", proc.returncode, stderr[:200])
    except asyncio.TimeoutError:
        logger.warning("ffmpeg remux timeout")
    except Exception as e:
        logger.warning("ffmpeg remux error: %s", e)
    return False


def post_process_download(
    path:       str,
    mime:       str,
    drm_keys:   Dict[str, str] = None,
    content_id: str = "",
) -> str:
    """
    After download, attempt format detection and decryption if needed.

    Returns the path to the best version of the file (may be the same path
    if no processing was needed or possible).
    """
    if not os.path.exists(path):
        return path

    detected = detect_format(path)
    logger.debug("post_process: %s detected=%s mime=%s", path, detected, mime)

    # PDF decryption
    if (mime == "application/pdf" or path.lower().endswith(".pdf")) and detected is None:
        dec = path + ".dec.pdf"
        if try_decrypt_pdf(path, dec):
            os.replace(dec, path)
            return path
        elif os.path.exists(dec):
            os.remove(dec)
        return path

    # Video: try DRM key decryption if format is undetected
    if detected is None and drm_keys and (
        mime.startswith("video/") or path.lower().endswith((".mkv", ".mp4", ".m4v"))
    ):
        dec = path + ".dec"
        if try_decrypt_with_drm_keys(path, dec, drm_keys, content_id):
            os.replace(dec, path)
        elif os.path.exists(dec):
            os.remove(dec)

    return path
