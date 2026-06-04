"""
AppX V2 bypass test script — run this to verify your setup before deploying.

Usage:
    cd /path/to/appx-uploader-bot
    python scripts/test_appx.py --cookie "token=eyJ..." --url "https://static-trans-v2.appx.co.in/..."

Tests:
  1. Token extraction from cookie string
  2. URL classification (appx / appx_v2 / hls / etc.)
  3. AppX V2 URL metadata parsing
  4. HLS manifest probe (tries to find .m3u8)
  5. AppX API endpoint probes (tries to get a signed URL)
  6. yt-dlp availability + version
  7. ffmpeg availability
"""

import argparse
import asyncio
import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ok(msg): print(f"  ✅  {msg}")
def _warn(msg): print(f"  ⚠️   {msg}")
def _fail(msg): print(f"  ❌  {msg}")
def _info(msg): print(f"  ℹ️   {msg}")
def _section(title): print(f"\n{'='*55}\n  {title}\n{'='*55}")


# ── Test 1: imports ────────────────────────────────────────────────────────────
def test_imports():
    _section("1. Module Imports")
    ok = True
    for mod in ["aiohttp", "aiofiles", "aiosqlite", "telegram"]:
        try:
            __import__(mod)
            _ok(f"{mod} — installed")
        except ImportError:
            _fail(f"{mod} — NOT installed  (pip install {mod})")
            ok = False
    try:
        import yt_dlp
        _ok(f"yt-dlp {yt_dlp.version.__version__} — installed")
    except ImportError:
        _fail("yt-dlp — NOT installed  (pip install yt-dlp)")
        ok = False
    try:
        import pikepdf
        _ok(f"pikepdf — installed")
    except ImportError:
        _warn("pikepdf — not installed (PDF decryption disabled)")
    try:
        from Crypto.Cipher import AES
        _ok("pycryptodome — installed")
    except ImportError:
        try:
            from Cryptodome.Cipher import AES
            _ok("pycryptodomex — installed")
        except ImportError:
            _warn("pycryptodome/x — not installed (AES decrypt disabled)")
    return ok


# ── Test 2: system binaries ────────────────────────────────────────────────────
def test_binaries():
    _section("2. System Binaries")
    import shutil
    ok = True
    for binary in ["ffmpeg", "ffprobe"]:
        path = shutil.which(binary)
        if path:
            _ok(f"{binary} → {path}")
        else:
            _warn(f"{binary} — not found (HLS merge / remux may fail)")
    return True


# ── Test 3: classify ───────────────────────────────────────────────────────────
def test_classify():
    _section("3. URL Classification")
    from bot.drm import classify

    cases = [
        ("https://static-trans-v2.appx.co.in/videos/x/360p/encrypted.mkv/encrypted.mkv",
         "appx_v2", "V2 video URL"),
        ("https://static-db-v2.appx.co.in/pdf/x.pdf?Signature=abc&Expires=123",
         "appx",    "AppX PDF (CloudFront signed)"),
        ("https://example.com/stream.m3u8",
         "hls",     "HLS manifest"),
        ("https://player.vimeo.com/video/123456",
         "vimeo",   "Vimeo"),
        ("https://youtu.be/abc123",
         "youtube", "YouTube short"),
    ]
    ok = True
    for url, expected, label in cases:
        got = classify(url)
        if got == expected:
            _ok(f"{label} → '{got}'")
        else:
            _fail(f"{label} → got '{got}', expected '{expected}'")
            ok = False
    return ok


# ── Test 4: V2 URL metadata ────────────────────────────────────────────────────
def test_v2_parse():
    _section("4. AppX V2 URL Metadata Parsing")
    from bot.v2_bypass import parse_v2_video_info, is_appx_v2_video, appx_dedup_path
    from bot.drm import appx_dedup_path as drm_dedup

    url = "https://static-trans-v2.appx.co.in/videos/akstechnicalclasses-data/3661794-1777913165/encrypted-c400d5/360p/encrypted.mkv/encrypted.mkv"
    ok = True

    if is_appx_v2_video(url):
        _ok("is_appx_v2_video() → True")
    else:
        _fail("is_appx_v2_video() returned False for V2 URL")
        ok = False

    info = parse_v2_video_info(url)
    if info:
        _ok(f"course_code  = {info['course_code']}")
        _ok(f"content_id   = {info['content_id']}")
        _ok(f"enc_hash     = {info['enc_hash']}")
        _ok(f"quality      = {info['quality']}")
    else:
        _fail("parse_v2_video_info() returned None")
        ok = False

    deduped = drm_dedup(url)
    if deduped and deduped != url:
        _ok(f"appx_dedup_path() → {deduped}")
    else:
        _warn("appx_dedup_path() — no deduplication needed or failed")
    return ok


# ── Test 5: token extraction ───────────────────────────────────────────────────
def test_token(cookie: str):
    _section("5. Cookie → Token Extraction")
    from bot.drm import extract_token
    token = extract_token(cookie)
    if token:
        _ok(f"Token extracted: {token[:20]}…")
        return token
    else:
        _fail("No Bearer token found in cookie string")
        _info("Expected format: 'token=eyJ...' or 'Bearer eyJ...'")
        return None


# ── Test 6: live V2 probes ─────────────────────────────────────────────────────
async def test_live_probes(url: str, token: str):
    _section("6. Live V2 Bypass Probes")
    import aiohttp
    from bot.v2_bypass import try_get_v2_hls_url, try_appx_v2_api, resolve_v2_best_url

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as sess:
        print("  → Probing HLS manifests…")
        hls = await try_get_v2_hls_url(sess, url, token)
        if hls:
            _ok(f"HLS manifest found: {hls}")
        else:
            _warn("No HLS manifest found (V2 may use direct MKV download)")

        print("  → Probing AppX API endpoints…")
        api_url = await try_appx_v2_api(sess, url, token)
        if api_url:
            _ok(f"API URL: {api_url[:90]}")
        else:
            _warn("No API URL returned (endpoints may require different auth scope)")

        best, url_type = await resolve_v2_best_url(sess, url, token)
        _info(f"Best URL [{url_type}]: {best[:100]}")


# ── Test 7: yt-dlp dry run ─────────────────────────────────────────────────────
def test_ytdlp_dryrun(url: str, token: str):
    _section("7. yt-dlp Dry Run (no download)")
    try:
        import yt_dlp
        headers = {
            "Authorization": f"Bearer {token}",
            "Referer":       "https://appx.co.in/",
            "Origin":        "https://appx.co.in",
        }
        opts = {
            "quiet": True, "simulate": True,
            "http_headers": headers,
            "allow_unplayable_formats": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                if info:
                    _ok(f"yt-dlp can access URL. Format: {info.get('ext', '?')}")
                else:
                    _warn("yt-dlp returned empty info (may still download OK)")
            except yt_dlp.utils.DownloadError as e:
                if "403" in str(e) or "401" in str(e):
                    _warn(f"Auth error: {e} — verify your token is current")
                else:
                    _warn(f"yt-dlp error: {e}")
    except Exception as e:
        _fail(f"yt-dlp test failed: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AppX V2 bypass test suite")
    parser.add_argument("--cookie", default="",
                        help="Your AppX cookie string (e.g. 'token=eyJ...')")
    parser.add_argument("--url",    default="",
                        help="AppX V2 video URL to test live bypass on")
    parser.add_argument("--quick",  action="store_true",
                        help="Only run offline tests (no live requests)")
    args = parser.parse_args()

    print("\n🔍 AppX V2 Bypass Test Suite — v4.3")
    print("   Verifying your bot setup before deployment\n")

    # Offline tests
    test_imports()
    test_binaries()
    test_classify()
    test_v2_parse()

    token = None
    if args.cookie:
        token = test_token(args.cookie)

    if not args.quick and args.url and token:
        asyncio.run(test_live_probes(args.url, token))
        test_ytdlp_dryrun(args.url, token)
    elif not args.quick and (args.url or args.cookie):
        _section("Skipping live tests")
        if not args.url:
            _warn("Provide --url to run live bypass probes")
        if not args.cookie:
            _warn("Provide --cookie to run live probes with auth")
    else:
        _section("Offline tests complete")
        _info("Run with --url and --cookie for live bypass testing")

    print("\n✅ Test suite finished.\n")


if __name__ == "__main__":
    main()
