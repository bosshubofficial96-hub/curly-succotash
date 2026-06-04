"""
Advanced DRM / CDN Bypass Engine v5.1 - FULLY FIXED
Complete AppX V2 Video Bypass with Multi-Strategy API Resolution
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION (Using environment variables for security)
# ============================================================================

APPX_API_BASE = os.getenv("APPX_API_BASE", "https://api.appx.co.in")
APPX_CDN_BASE = os.getenv("APPX_CDN_BASE", "https://cdn.appx.co.in")
APPX_LOGIN_URL = f"{APPX_API_BASE}/api/v1/auth/login"

# CDN Host Pools
APPX_CDN_HOSTS_V1 = [
    "static-db.appx.co.in",
    "cdn.appx.co.in", 
    "media.appx.co.in",
    "static.appx.co.in",
]

APPX_CDN_HOSTS_V2 = [
    "static-trans-v2.appx.co.in",
    "static-db-v2.appx.co.in",
    "appxcdn.appx.co.in",
    "d1bsb8xfl4oazp.cloudfront.net",
]

APPX_S3_BUCKETS = [
    "appxcontent.s3.ap-south-1.amazonaws.com",
    "appxlectures.s3.ap-south-1.amazonaws.com",
    "appx-pdf-keyset.s3.ap-south-1.amazonaws.com",
]

# Headers template
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Blocked hosts for security
BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal", "169.254.169.254",
    "internal", "local"
}

PRIVATE_IP_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                       "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                       "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                       "172.29.", "172.30.", "172.31.")

# URL validation regex
URL_REGEX = re.compile(r"^https?://[^\s<>\"{}|\\^`\[\]]+$")

# V2 Video URL Pattern
V2_VIDEO_PATTERN = re.compile(
    r"/videos/([^/]+)/([^/]+)/",
    re.IGNORECASE
)

# Quality extraction pattern
QUALITY_PATTERN = re.compile(r"/(360p|480p|720p|1080p|1440p|2k|4k|auto)/", re.IGNORECASE)

# Rate limiting settings
MAX_REQUESTS_PER_MINUTE = 30
MAX_CONCURRENT_REQUESTS = 10

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_valid_url(url: str) -> bool:
    """Validate URL format and ensure it's not internal/blocked."""
    url = url.strip()
    if not url or not URL_REGEX.match(url):
        return False
    
    try:
        host = urlparse(url).hostname or ""
        if host in BLOCKED_HOSTS:
            return False
        if any(host.startswith(prefix) for prefix in PRIVATE_IP_PREFIXES):
            return False
        return True
    except:
        return False

def classify_url(url: str) -> str:
    """Classify URL type for specialized handling."""
    url_lower = url.lower()
    
    if any(domain in url_lower for domain in ["appx.co.in", "appx-pdf-keyset", "appxcdn"]):
        return "appx"
    if ".m3u8" in url_lower:
        return "hls"
    if ".mpd" in url_lower:
        return "dash"
    if any(s in url_lower for s in ["x-amz-signature", "awsaccesskeyid", "s3.amazonaws.com"]):
        return "s3"
    if "storage.googleapis.com" in url_lower:
        return "gcs"
    if "jwplatform.com" in url_lower or "jwpsrv.com" in url_lower:
        return "jwplatform"
    if "vimeo.com" in url_lower:
        return "vimeo"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "drive.google.com" in url_lower:
        return "gdrive"
    
    return "generic"

def decode_base64_url(s: str) -> Optional[str]:
    """Decode URL-safe base64 string."""
    s = s.replace("-", "+").replace("_", "/")
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    try:
        return base64.b64decode(s).decode("utf-8")
    except:
        return None

def decode_base64_bytes(s: str) -> Optional[bytes]:
    """Decode URL-safe base64 to bytes."""
    s = s.replace("-", "+").replace("_", "/")
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    try:
        return base64.b64decode(s)
    except:
        return None

def extract_token_from_cookie(cookie: str) -> Optional[str]:
    """Extract JWT token from cookie string."""
    if not cookie:
        return None
    
    for part in cookie.split(";"):
        part = part.strip()
        
        # Check for token keys
        for key in ["token", "authToken", "auth_token", "jwt", "access_token", "bearer"]:
            if part.lower().startswith(key.lower() + "="):
                return part.split("=", 1)[1].strip()
        
        # Direct JWT detection
        if part.startswith("eyJ") and "." in part:
            return part
    
    return None

def generate_device_id() -> str:
    """Generate unique device fingerprint."""
    timestamp = str(int(time.time() * 1000))
    random_bytes = os.urandom(16)
    random_str = base64.b64encode(random_bytes).decode('ascii')
    fingerprint = hashlib.md5(f"{timestamp}{random_str}".encode()).hexdigest()
    return fingerprint

def generate_request_id() -> str:
    """Generate unique request ID for tracking."""
    return hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:16]

# ============================================================================
# URL TRANSFORMATION FUNCTIONS
# ============================================================================

def strip_cloudfront_params(url: str) -> str:
    """Remove CloudFront signing parameters from URL."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    
    params_to_remove = [
        "Signature", "KeyName", "Expires", "URLPrefix", "Policy", "Key-Pair-Id",
        "signature", "keyname", "expires", "urlprefix", "policy", "key-pair-id",
        "X-Amz-Signature", "X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires",
        "X-Amz-SignedHeaders", "X-Amz-Security-Token", "X-Amz-Algorithm"
    ]
    
    for param in params_to_remove:
        query_params.pop(param, None)
    
    new_query = urlencode({k: v[0] for k, v in query_params.items()}) if query_params else ""
    return urlunparse(parsed._replace(query=new_query))

def decode_url_prefix(url: str) -> Optional[str]:
    """Decode CloudFront URLPrefix parameter to get base directory URL."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    url_prefix = query_params.get("URLPrefix") or query_params.get("urlprefix")
    if not url_prefix:
        return None
    
    decoded = decode_base64_url(url_prefix[0])
    if decoded and decoded.startswith("http"):
        return decoded
    
    return None

def get_direct_file_url(url: str) -> Optional[str]:
    """Extract direct file URL from CloudFront signed URL."""
    prefix = decode_url_prefix(url)
    if not prefix:
        return None
    
    path = urlparse(url).path
    filename = path.rstrip("/").split("/")[-1]
    
    if not filename:
        return None
    
    return f"{prefix.rstrip('/')}/{filename}"

def fix_duplicated_filename(url: str) -> Optional[str]:
    """
    Fix AppX V2 URL bug where filename is duplicated.
    Example: /360p/encrypted.mkv/encrypted.mkv -> /360p/encrypted.mkv
    """
    parsed = urlparse(url)
    path_parts = parsed.path.rstrip("/").split("/")
    
    if len(path_parts) >= 2 and path_parts[-1] == path_parts[-2]:
        clean_path = "/".join(path_parts[:-1])
        return urlunparse(parsed._replace(path=clean_path))
    
    return None

def extract_s3_url(url: str) -> Optional[str]:
    """Convert CloudFront URL to direct S3 URL if possible."""
    parsed = urlparse(url)
    
    # If already S3 URL, return as-is
    if ".s3." in parsed.netloc and ".amazonaws.com" in parsed.netloc:
        return url
    
    # Try to extract bucket from path
    if "cloudfront.net" in parsed.netloc:
        path_parts = parsed.path.lstrip("/").split("/")
        if len(path_parts) >= 2:
            bucket = path_parts[0]
            # AppX typically uses ap-south-1 region
            s3_url = f"https://{bucket}.s3.ap-south-1.amazonaws.com/{'/'.join(path_parts[1:])}"
            return s3_url
    
    return None

def rotate_cdn_hosts(url: str, use_v2: bool = False) -> List[str]:
    """Generate CDN host rotation URLs."""
    parsed = urlparse(url)
    path = parsed.path
    
    hosts = APPX_CDN_HOSTS_V2 if use_v2 else APPX_CDN_HOSTS_V1
    return [f"https://{host}{path}" for host in hosts]

def add_token_param(url: str, token: str) -> List[str]:
    """Add token as query parameter in various ways."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    
    results = []
    param_names = ["token", "auth", "access_token", "jwt", "t", "api_key", "key", "authorization"]
    
    for param in param_names:
        new_params = {k: v[0] for k, v in query_params.items()}
        new_params[param] = token
        new_url = urlunparse(parsed._replace(query=urlencode(new_params)))
        results.append(new_url)
    
    return results

# ============================================================================
# V2 URL PARSING
# ============================================================================

def parse_v2_video_url(url: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse AppX V2 video URL to extract courseCode, contentId, and quality.
    
    Returns:
        Tuple of (course_code, content_id, quality) or None if parsing fails
    """
    try:
        parsed = urlparse(url)
        path = parsed.path
        
        # Extract courseCode and contentId
        match = V2_VIDEO_PATTERN.search(path)
        if not match:
            logger.debug(f"Failed to parse V2 URL pattern: {path[:100]}")
            return None
        
        course_code = match.group(1)
        content_id = match.group(2)
        
        # Clean up content_id (remove any query params or fragments)
        content_id = content_id.split("?")[0].split("#")[0]
        
        # Extract quality from path
        quality_match = QUALITY_PATTERN.search(path)
        quality = quality_match.group(1) if quality_match else "auto"
        
        logger.debug(f"Parsed V2 URL - Course: {course_code}, ContentID: {content_id}, Quality: {quality}")
        return (course_code, content_id, quality)
        
    except Exception as e:
        logger.debug(f"Error parsing V2 URL: {e}")
        return None

# ============================================================================
# API RESOLVERS
# ============================================================================

async def resolve_v1_api(session: aiohttp.ClientSession, path: str, token: str) -> Optional[str]:
    """AppX V1 API resolver - gets fresh signed URL."""
    endpoints = [
        (f"{APPX_API_BASE}/api/v1/media/getUrl", {"path": path.lstrip("/")}),
        (f"{APPX_API_BASE}/api/v1/content/url", {"resource": path.lstrip("/"), "type": "video"}),
    ]
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
        "X-Requested-With": "XMLHttpRequest",
    }
    
    for endpoint, payload in endpoints:
        try:
            async with session.post(
                endpoint, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    
                    # Extract URL from various response formats
                    url = (data.get("url") or 
                          data.get("data", {}).get("url") or
                          data.get("signedUrl") or
                          data.get("downloadUrl") or
                          data.get("streamUrl"))
                    
                    if url and url.startswith("http"):
                        logger.info(f"V1 API resolved: {url[:80]}...")
                        return url
        except asyncio.TimeoutError:
            logger.debug(f"Timeout for V1 API {endpoint}")
        except Exception as e:
            logger.debug(f"V1 API {endpoint} failed: {e}")
    
    return None

async def resolve_v2_stream_api(session: aiohttp.ClientSession, url: str, token: str) -> Optional[str]:
    """AppX V2 Stream API resolver."""
    endpoints = [
        f"{APPX_API_BASE}/api/v2/content/stream",
        f"{APPX_API_BASE}/api/v2/media/resolve",
        f"{APPX_API_BASE}/api/v2/content/resolve",
    ]
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
        "X-Device-ID": generate_device_id(),
    }
    
    for endpoint in endpoints:
        try:
            payload = {"url": url} if "stream" in endpoint else {"resourceUrl": url}
            
            async with session.post(
                endpoint, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    
                    resolved = (data.get("url") or
                               data.get("downloadUrl") or
                               data.get("streamUrl") or
                               data.get("data", {}).get("url") or
                               data.get("result", {}).get("url"))
                    
                    if resolved and resolved.startswith("http"):
                        logger.info(f"V2 Stream API resolved via {endpoint}")
                        return resolved
        except asyncio.TimeoutError:
            logger.debug(f"Timeout for V2 Stream API {endpoint}")
        except Exception as e:
            logger.debug(f"V2 Stream API {endpoint} failed: {e}")
    
    return None

async def resolve_v2_content_api(session: aiohttp.ClientSession, url: str, token: str) -> Optional[str]:
    """
    AppX V2 Content API resolver.
    Extracts courseCode and contentId, then calls multiple API endpoints.
    """
    parsed = parse_v2_video_url(url)
    if not parsed:
        logger.debug("Cannot parse V2 URL, falling back to stream API")
        return await resolve_v2_stream_api(session, url, token)
    
    course_code, content_id, quality = parsed
    logger.info(f"V2 Content API - Course: {course_code}, ContentID: {content_id}, Quality: {quality}")
    
    # Prepare headers with anti-detection
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
        "X-Requested-With": "XMLHttpRequest",
        "X-Device-ID": generate_device_id(),
        "X-Request-ID": generate_request_id(),
        "X-Course-Code": course_code,
        "X-Content-ID": content_id,
    }
    
    # API endpoints with various payload structures
    api_calls = [
        # V2 Content URL endpoint
        (f"{APPX_API_BASE}/api/v2/content/url", "POST",
         {"contentId": content_id, "courseCode": course_code, "quality": quality}),
        
        # V2 Media URL endpoint
        (f"{APPX_API_BASE}/api/v2/media/url", "POST",
         {"contentId": content_id, "type": "video", "quality": quality}),
        
        # V2 Lecture URL endpoint
        (f"{APPX_API_BASE}/api/v2/lectures/url", "POST",
         {"lectureId": content_id, "courseCode": course_code}),
        
        # V2 Get Signed URL
        (f"{APPX_API_BASE}/api/v2/content/getSignedUrl", "POST",
         {"url": url, "contentId": content_id, "courseCode": course_code}),
        
        # V1 Fallback
        (f"{APPX_API_BASE}/api/v1/content/url", "POST",
         {"contentId": content_id, "type": "video"}),
        
        # GET endpoints
        (f"{APPX_API_BASE}/api/v2/content/{content_id}", "GET", None),
        (f"{APPX_API_BASE}/api/v2/lectures/{content_id}/url", "GET", None),
    ]
    
    for endpoint, method, payload in api_calls:
        try:
            if method == "POST":
                async with session.post(
                    endpoint, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        resolved_url = extract_url_from_response(data)
                        if resolved_url:
                            logger.info(f"V2 Content API resolved via {endpoint}")
                            return resolved_url
            else:
                async with session.get(
                    endpoint, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        resolved_url = extract_url_from_response(data)
                        if resolved_url:
                            logger.info(f"V2 Content API resolved via GET {endpoint}")
                            return resolved_url
                            
        except asyncio.TimeoutError:
            logger.debug(f"Timeout for {endpoint}")
        except Exception as e:
            logger.debug(f"API call {endpoint} failed: {e}")
    
    return None

async def resolve_graphql_api(session: aiohttp.ClientSession, url: str, token: str) -> Optional[str]:
    """GraphQL API resolver for newer AppX versions."""
    parsed = parse_v2_video_url(url)
    if not parsed:
        return None
    
    course_code, content_id, quality = parsed
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
    }
    
    graphql_url = f"{APPX_API_BASE}/graphql"
    
    queries = [
        {
            "query": """
                query GetLectureUrl($contentId: String!, $courseCode: String!) {
                    getLectureUrl(contentId: $contentId, courseCode: $courseCode) {
                        url
                        signedUrl
                        expiry
                    }
                }
            """,
            "variables": {"contentId": content_id, "courseCode": course_code}
        },
        {
            "query": """
                query GetMediaUrl($contentId: String!, $type: String!) {
                    getMediaUrl(contentId: $contentId, type: $type) {
                        url
                        signedUrl
                        quality
                    }
                }
            """,
            "variables": {"contentId": content_id, "type": "video"}
        }
    ]
    
    for query_data in queries:
        try:
            async with session.post(
                graphql_url, json=query_data, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", {})
                    
                    for key in result:
                        url_data = result[key]
                        if isinstance(url_data, dict):
                            video_url = url_data.get("url") or url_data.get("signedUrl")
                            if video_url and video_url.startswith("http"):
                                logger.info(f"GraphQL resolved via {key}")
                                return video_url
        except asyncio.TimeoutError:
            logger.debug(f"Timeout for GraphQL query")
        except Exception as e:
            logger.debug(f"GraphQL query failed: {e}")
    
    return None

def extract_url_from_response(data: Dict) -> Optional[str]:
    """Extract URL from various API response formats."""
    if not isinstance(data, dict):
        return None
    
    # Direct fields
    for field in ["url", "signedUrl", "downloadUrl", "streamUrl", "hlsUrl", "mpdUrl", "cdnUrl"]:
        if data.get(field) and isinstance(data[field], str) and data[field].startswith("http"):
            return data[field]
    
    # Nested in data/result objects
    for nested in ["data", "result", "response", "video", "media", "lecture"]:
        if isinstance(data.get(nested), dict):
            for field in ["url", "signedUrl", "downloadUrl", "streamUrl"]:
                if data[nested].get(field) and isinstance(data[nested][field], str) and data[nested][field].startswith("http"):
                    return data[nested][field]
    
    return None

# ============================================================================
# LOGIN FUNCTION
# ============================================================================

async def appx_login(session: aiohttp.ClientSession, email: str, password: str) -> Optional[str]:
    """Login to AppX and return cookie string with token."""
    if not email or not password:
        logger.error("Email and password are required for login")
        return None
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
        "User-Agent": BASE_HEADERS["User-Agent"]
    }
    
    try:
        async with session.post(
            APPX_LOGIN_URL,
            json={"email": email, "password": password},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            if response.status != 200:
                logger.warning(f"Login failed with status {response.status}")
                return None
            
            data = await response.json(content_type=None)
            
            # Extract token from response
            token = (data.get("token") or 
                    data.get("access_token") or 
                    data.get("data", {}).get("token") or
                    data.get("data", {}).get("access_token"))
            
            if token:
                logger.info("AppX login successful")
                return f"token={token}; authToken={token}"
            else:
                logger.warning("No token in login response")
                return None
                
    except asyncio.TimeoutError:
        logger.error("Login timeout")
        return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        return None

# ============================================================================
# PROBE FUNCTIONS
# ============================================================================

async def probe_url(session: aiohttp.ClientSession, url: str, headers: Dict, proxy: str = None) -> bool:
    """Check if URL is accessible with given headers."""
    timeout = aiohttp.ClientTimeout(total=8)
    
    # Try HEAD request first
    try:
        async with session.head(
            url, headers=headers, allow_redirects=True,
            proxy=proxy, timeout=timeout, ssl=False
        ) as response:
            if response.status in [200, 206, 302, 304, 403]:
                return True
    except:
        pass
    
    # Try GET with range
    range_headers = {**headers, "Range": "bytes=0-0"}
    try:
        async with session.get(
            url, headers=range_headers, allow_redirects=True,
            proxy=proxy, timeout=timeout, ssl=False
        ) as response:
            if response.status in [200, 206]:
                return True
    except:
        pass
    
    # Final attempt with full GET
    try:
        async with session.get(
            url, headers=headers, allow_redirects=True,
            proxy=proxy, timeout=timeout, ssl=False
        ) as response:
            if response.status == 200:
                await response.content.read(1)
                return True
    except:
        pass
    
    return False

async def probe_batch(session: aiohttp.ClientSession, candidates: List[Tuple[str, Dict, str]], 
                      proxy: str = None, batch_size: int = 3) -> Optional[Tuple[str, Dict, str]]:
    """Probe multiple URLs in parallel batches with connection limiting."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    
    async def probe_with_limit(url, headers, label):
        async with semaphore:
            success = await probe_url(session, url, headers, proxy)
            return success, url, headers, label
    
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        tasks = [probe_with_limit(url, headers, label) for url, headers, label in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, tuple) and result[0] is True:
                return (result[1], result[2], result[3])
    
    return None

# ============================================================================
# DRM KEY MANAGEMENT
# ============================================================================

async def get_merged_drm_keys(db=None) -> Dict[str, str]:
    """Merge DRM keys from config and database safely."""
    keys = {}
    
    # Try to load from environment variables
    env_keys = os.getenv("DRM_KEYS", "")
    if env_keys:
        try:
            for key_pair in env_keys.split(","):
                if ":" in key_pair:
                    k_id, k_val = key_pair.split(":", 1)
                    keys[k_id.strip()] = k_val.strip()
        except Exception as e:
            logger.debug(f"Failed to parse DRM_KEYS from env: {e}")
    
    # Try to load from config file if available
    try:
        from config.settings import DRM_KEYS
        if isinstance(DRM_KEYS, dict):
            keys.update(DRM_KEYS)
    except (ImportError, ModuleNotFoundError):
        logger.debug("DRM_KEYS not found in config.settings")
    except Exception as e:
        logger.debug(f"Error loading DRM_KEYS from config: {e}")
    
    # Load from database if provided
    if db and hasattr(db, 'get_drm_keys'):
        try:
            db_keys = await db.get_drm_keys()
            if isinstance(db_keys, dict):
                keys.update(db_keys)
        except Exception as e:
            logger.debug(f"Failed to load DB DRM keys: {e}")
    
    return keys

# ============================================================================
# MAIN DRM RESOLVER CLASS
# ============================================================================

class RateLimiter:
    """Simple rate limiter for API requests."""
    
    def __init__(self, max_requests_per_minute: int = MAX_REQUESTS_PER_MINUTE):
        self.max_requests = max_requests_per_minute
        self.requests = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """Acquire permission to make a request."""
        async with self.lock:
            now = time.time()
            # Remove requests older than 1 minute
            self.requests = [t for t in self.requests if now - t < 60]
            
            if len(self.requests) >= self.max_requests:
                # Wait until the oldest request expires
                wait_time = 60 - (now - self.requests[0]) + 0.1
                await asyncio.sleep(wait_time)
                return await self.acquire()
            
            self.requests.append(now)

class DRMResolver:
    """Main DRM resolver with multi-strategy bypass."""
    
    def __init__(self, session: aiohttp.ClientSession, cookie: str = "", 
                 drm_keys: Dict[str, str] = None, proxy: str = None):
        self.session = session
        self.cookie = cookie
        self.drm_keys = drm_keys or {}
        self.proxy = proxy
        self.token = extract_token_from_cookie(cookie) if cookie else None
        self.token_expiry = None
        self.rate_limiter = RateLimiter()
    
    def _build_headers(self, extra: Dict = None) -> Dict:
        """Build request headers with authentication."""
        headers = BASE_HEADERS.copy()
        
        if self.cookie:
            headers["Cookie"] = self.cookie
        
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        
        if extra:
            headers.update(extra)
        
        return headers
    
    async def _check_rate_limit(self):
        """Apply rate limiting."""
        await self.rate_limiter.acquire()
    
    async def resolve(self, url: str) -> Tuple[str, Dict, str]:
        """Resolve URL to accessible format."""
        await self._check_rate_limit()
        
        url_type = classify_url(url)
        headers = self._build_headers()
        
        # Non-AppX URLs return as-is
        if url_type != "appx":
            return url, headers, url_type
        
        # Resolve AppX URL
        resolved_url, resolved_headers = await self._resolve_appx(url, headers)
        return resolved_url, resolved_headers, url_type
    
    async def _resolve_appx(self, url: str, base_headers: Dict) -> Tuple[str, Dict]:
        """
        Multi-strategy AppX URL resolver with V2 bypass.
        
        Strategies:
        S1: Original + auth headers
        S2: Bearer only (no cookie)
        S3: Decoded URL prefix
        S4: Direct file URL
        S5: Stripped CloudFront params
        S6: V1 API fresh URL
        S7: CDN host rotation
        S8: Token as query param
        S9: Auth headers + V2 CDN
        S10: V2 Stream API
        S11: Deduplicated path
        S12: V2 Content API
        S13: GraphQL API
        S14: Direct S3 URL
        """
        
        # ===== PHASE 1: Parallel API Resolution =====
        v1_url = None
        v2_stream_url = None
        v2_content_url = None
        graphql_url = None
        
        if self.token:
            path = urlparse(url).path
            
            # Run all API resolvers in parallel
            results = await asyncio.gather(
                resolve_v1_api(self.session, path, self.token),
                resolve_v2_stream_api(self.session, url, self.token),
                resolve_v2_content_api(self.session, url, self.token),
                resolve_graphql_api(self.session, url, self.token),
                return_exceptions=True
            )
            
            v1_url = results[0] if isinstance(results[0], str) else None
            v2_stream_url = results[1] if isinstance(results[1], str) else None
            v2_content_url = results[2] if isinstance(results[2], str) else None
            graphql_url = results[3] if isinstance(results[3], str) else None
        
        # ===== PHASE 2: Structural Transformations =====
        decoded_prefix = decode_url_prefix(url)
        direct_file_url = get_direct_file_url(url)
        stripped_url = strip_cloudfront_params(url)
        deduped_url = fix_duplicated_filename(url)
        s3_url = extract_s3_url(url)
        
        # ===== PHASE 3: Build Priority Order =====
        priority_urls = []
        
        # API results (highest priority)
        if v2_content_url:
            api_headers = self._build_headers({
                "X-API-Source": "v2-content",
                "X-Resolved-At": str(int(time.time()))
            })
            priority_urls.append((v2_content_url, api_headers, "V2-Content-API"))
        
        if graphql_url:
            api_headers = self._build_headers({
                "X-API-Source": "graphql",
                "X-Resolved-At": str(int(time.time()))
            })
            priority_urls.append((graphql_url, api_headers, "GraphQL-API"))
        
        if v2_stream_url:
            api_headers = self._build_headers({
                "X-API-Source": "v2-stream",
                "X-Resolved-At": str(int(time.time()))
            })
            priority_urls.append((v2_stream_url, api_headers, "V2-Stream-API"))
        
        if v1_url:
            api_headers = self._build_headers({
                "X-API-Source": "v1-api",
                "X-Resolved-At": str(int(time.time()))
            })
            priority_urls.append((v1_url, api_headers, "V1-API"))
        
        # Structural fixes
        if deduped_url:
            priority_urls.append((deduped_url, base_headers, "Deduped-Path"))
        if s3_url:
            priority_urls.append((s3_url, base_headers, "Direct-S3"))
        if direct_file_url and direct_file_url != url:
            priority_urls.append((direct_file_url, base_headers, "Direct-File"))
        if stripped_url != url:
            priority_urls.append((stripped_url, base_headers, "Stripped-Params"))
        
        # ===== PHASE 4: Build Complete Candidate List =====
        candidates = []
        
        # S1: Original URL
        candidates.append((url, base_headers, "S1-Original"))
        
        # S2: Bearer only
        if self.token:
            headers_no_cookie = {k: v for k, v in base_headers.items() if k != "Cookie"}
            candidates.append((url, headers_no_cookie, "S2-Bearer-Only"))
        
        # S3: Decoded prefix
        if decoded_prefix and decoded_prefix.startswith("http"):
            candidates.append((decoded_prefix, base_headers, "S3-Decoded-Prefix"))
        
        # S4: Direct file URL
        if direct_file_url and direct_file_url != url:
            candidates.append((direct_file_url, base_headers, "S4-Direct-File"))
        
        # S5: Stripped params
        if stripped_url != url:
            candidates.append((stripped_url, base_headers, "S5-Stripped"))
        
        # S6: V1 API
        if v1_url:
            candidates.append((v1_url, base_headers, "S6-V1-API"))
        
        # S7: CDN rotation
        for cdn_url in rotate_cdn_hosts(url, use_v2=False):
            candidates.append((cdn_url, base_headers, "S7-CDN-Rotation"))
        
        # S8: Token as param
        if self.token:
            headers_no_cookie = {k: v for k, v in base_headers.items() if k != "Cookie"}
            for token_url in add_token_param(url, self.token):
                candidates.append((token_url, headers_no_cookie, "S8-Token-Param"))
        
        # S9: V2 headers + CDN
        if self.token:
            v2_headers = dict(base_headers)
            v2_headers["X-Auth-Token"] = self.token
            v2_headers["X-API-Key"] = self.token
            candidates.append((url, v2_headers, "S9-V2-Headers"))
            
            for v2_url in rotate_cdn_hosts(url, use_v2=True):
                candidates.append((v2_url, v2_headers, "S9-V2-CDN"))
        
        # S10: V2 Stream API
        if v2_stream_url:
            candidates.append((v2_stream_url, base_headers, "S10-V2-Stream"))
        
        # S11: Deduped path
        if deduped_url:
            candidates.append((deduped_url, base_headers, "S11-Deduped"))
            candidates.append((strip_cloudfront_params(deduped_url), base_headers, "S11-Deduped-Stripped"))
        
        # S12: V2 Content API
        if v2_content_url:
            candidates.append((v2_content_url, base_headers, "S12-V2-Content"))
        
        # S13: GraphQL
        if graphql_url:
            candidates.append((graphql_url, base_headers, "S13-GraphQL"))
        
        # S14: S3 direct
        if s3_url:
            candidates.append((s3_url, base_headers, "S14-Direct-S3"))
        
        # ===== PHASE 5: Parallel Probing =====
        winner = await probe_batch(self.session, candidates, proxy=self.proxy, batch_size=3)
        
        if winner:
            winning_url, winning_headers, strategy = winner
            logger.info(f"✅ AppX Bypass Success - Strategy: {strategy}")
            logger.info(f"   URL: {winning_url[:100]}...")
            return winning_url, winning_headers
        
        # ===== PHASE 6: Fallback to best priority URL =====
        if priority_urls:
            best_url, best_headers, strategy = priority_urls[0]
            logger.warning(f"⚠️ No working URL found, using best priority: {strategy}")
            return best_url, best_headers
        
        # Ultimate fallback
        logger.warning(f"❌ No bypass found, returning original URL")
        return url, base_headers

# ============================================================================
# DOWNLOADER FUNCTIONS
# ============================================================================

async def download_stream(
    url: str,
    output_path: str,
    headers: Dict = None,
    cookies_file: str = None,
    drm_keys: Dict = None,
    proxy: str = None,
    progress_hook: Callable = None
) -> bool:
    """Download stream using yt-dlp."""
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed. Run: pip install yt-dlp")
        return False
    
    ydl_opts = {
        "outtmpl": output_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "retries": 10,
        "fragment_retries": 15,
        "skip_unavailable_fragments": True,
        "ignoreerrors": False,
        "http_headers": headers or {},
        "hls_use_mpegts": True,
        "concurrent_fragment_downloads": 5,
        "buffersize": 256 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "socket_timeout": 30,
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }
    
    if cookies_file and os.path.isfile(cookies_file):
        ydl_opts["cookiefile"] = cookies_file
    
    if proxy:
        ydl_opts["proxy"] = proxy
    
    if drm_keys:
        ydl_opts["allow_unplayable_formats"] = True
        ydl_opts["fixup"] = "never"
    
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]
    
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Run in executor to avoid blocking
            result = await loop.run_in_executor(None, lambda: ydl.download([url]))
            return result == 0
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False

def decrypt_pdf(input_path: str, output_path: str, password_list: List[str] = None) -> bool:
    """Attempt to decrypt password-protected PDF with provided passwords."""
    try:
        import pikepdf
    except ImportError:
        logger.error("pikepdf not installed. Run: pip install pikepdf")
        return False
    
    # Use provided passwords or load from environment
    if password_list is None:
        env_passwords = os.getenv("PDF_PASSWORDS", "")
        password_list = [p.strip() for p in env_passwords.split(",")] if env_passwords else [""]
    
    # Add common passwords as fallback
    common_passwords = ["", "appx", "appxco", "appx123", "123456", "password",
                        "appxlearn", "learn", "course", "admin", "student"]
    
    all_passwords = list(set(password_list + common_passwords))
    
    for password in all_passwords:
        try:
            with pikepdf.open(input_path, password=password) as pdf:
                pdf.save(output_path)
            logger.info(f"PDF decrypted successfully")
            return True
        except pikepdf.PasswordError:
            continue
        except Exception as e:
            logger.error(f"PDF error: {e}")
            break
    
    logger.warning(f"Could not decrypt PDF with any known password")
    return False

# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def main():
    """Main entry point for the DRM bypass engine."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Advanced DRM/CDN Bypass Engine")
    parser.add_argument("--url", type=str, help="URL to resolve")
    parser.add_argument("--cookie", type=str, help="Cookie string for authentication")
    parser.add_argument("--email", type=str, help="Email for login")
    parser.add_argument("--password", type=str, help="Password for login")
    parser.add_argument("--proxy", type=str, help="Proxy URL (e.g., http://proxy:8080)")
    parser.add_argument("--output", type=str, help="Output file path for download")
    parser.add_argument("--download", action="store_true", help="Download the resolved stream")
    
    args = parser.parse_args()
    
    if not args.url:
        print("Error: URL is required")
        parser.print_help()
        return
    
    async with aiohttp.ClientSession() as session:
        cookie = args.cookie
        
        # Try to login if credentials provided
        if args.email and args.password:
            cookie = await appx_login(session, args.email, args.password)
            if not cookie:
                print("Login failed")
                return
        
        # Initialize resolver
        resolver = DRMResolver(session, cookie=cookie, proxy=args.proxy)
        
        # Resolve URL
        print(f"\n🔍 Resolving URL: {args.url}")
        resolved_url, headers, url_type = await resolver.resolve(args.url)
        
        print(f"\n✅ Resolution Result:")
        print(f"   Type: {url_type}")
        print(f"   URL: {resolved_url}")
        print(f"   Headers: {json.dumps(headers, indent=2)}")
        
        # Download if requested
        if args.download and args.output:
            print(f"\n📥 Downloading to: {args.output}")
            success = await download_stream(
                resolved_url, args.output, headers, proxy=args.proxy
            )
            if success:
                print("✅ Download completed successfully")
            else:
                print("❌ Download failed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        logger.exception("Fatal error in main")
