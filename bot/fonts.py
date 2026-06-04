"""Unicode styled text helpers for Telegram messages."""

_BOLD_U = {chr(c): chr(0x1D400 + c - 65) for c in range(65, 91)}
_BOLD_L = {chr(c): chr(0x1D41A + c - 97) for c in range(97, 123)}
_BOLD_D = {str(d): chr(0x1D7CE + d) for d in range(10)}
_BOLD   = {**_BOLD_U, **_BOLD_L, **_BOLD_D}

_BI_U = {chr(c): chr(0x1D468 + c - 65) for c in range(65, 91)}
_BI_L = {chr(c): chr(0x1D482 + c - 97) for c in range(97, 123)}
_BI   = {**_BI_U, **_BI_L}

_SC_U = {chr(c): chr(0x1D4D0 + c - 65) for c in range(65, 91)}
_SC_L = {chr(c): chr(0x1D4EA + c - 97) for c in range(97, 123)}
_SC   = {**_SC_U, **_SC_L}

_FW_U = {chr(c): chr(0xFF21 + c - 65) for c in range(65, 91)}
_FW_L = {chr(c): chr(0xFF41 + c - 97) for c in range(97, 123)}
_FW_D = {str(d): chr(0xFF10 + d) for d in range(10)}
_FW   = {**_FW_U, **_FW_L, **_FW_D}

_CAPS = {
    'a':'ᴀ','b':'ʙ','c':'ᴄ','d':'ᴅ','e':'ᴇ','f':'ꜰ','g':'ɢ','h':'ʜ',
    'i':'ɪ','j':'ᴊ','k':'ᴋ','l':'ʟ','m':'ᴍ','n':'ɴ','o':'ᴏ','p':'ᴘ',
    'q':'Q','r':'ʀ','s':'ꜱ','t':'ᴛ','u':'ᴜ','v':'ᴠ','w':'ᴡ','x':'x',
    'y':'ʏ','z':'ᴢ',
}

_MON_U = {chr(c): chr(0x1D670 + c - 65) for c in range(65, 91)}
_MON_L = {chr(c): chr(0x1D68A + c - 97) for c in range(97, 123)}
_MON_D = {str(d): chr(0x1D7F6 + d) for d in range(10)}
_MON   = {**_MON_U, **_MON_L, **_MON_D}

def _ap(t: str, m: dict) -> str: return "".join(m.get(c, c) for c in t)

def bold(t: str)       -> str: return _ap(t, _BOLD)
def bold_italic(t: str)-> str: return _ap(t, _BI)
def script(t: str)     -> str: return _ap(t, _SC)
def smallcaps(t: str)  -> str: return _ap(t.lower(), _CAPS)
def fullwidth(t: str)  -> str: return _ap(t, _FW)
def mono(t: str)       -> str: return _ap(t, _MON)

DIVIDER  = "━" * 28
THIN     = "─" * 28
DOTLINE  = "· " * 14
APP_NAME = bold("AppX") + " " + bold_italic("Uploader Bot")
TAGLINE  = script("Advanced DRM Bypass System")
