"""Persistent, operator-authenticated EVA UI connector.

The operator signs in manually in installed Google Chrome. Scraping stays scoped to bookings
that match the single recording being prepared, then looks up only linked IDs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote, unquote

from matching import match_bookings, group_sessions, BAHRAIN, _is_cancelled
from app_paths import DATA, settings, save_settings

LOG = logging.getLogger('eva')

BASE_URL = "https://admin.eva.gg"
PROFILE_DIR = DATA / 'EvaBrowserProfile'                 # Google Chrome profile (as in earlier versions)
BRAVE_PROFILE_DIR = DATA / 'EvaBrowserProfileBrave'      # Brave keeps its own profile, so the two never clash
_LOCK = threading.RLock()
_CONTEXT = None
_LAST_ERROR = None
SESSION_FILE = DATA / 'eva_admin_session.bin'
CDP_FILE = DATA / 'eva_browser.json'                     # port of the Brave window EVA started (so it can be re-used)
_BROWSER_PROC = None
_MODE = None              # 'hidden' (background connection, no window) or 'visible' (sign-in window) or None
_LAST_LAUNCH = {}
BRAVE_START_TIMEOUT = 30
_LAUNCH_FAILED_AT = 0.0
LAUNCH_COOLDOWN = 600      # seconds the background sync waits before trying to open the browser again
_SESSION_SAVED_AT = 0.0
DEFAULT_VENUES = ('bahrain', 'marassi')


# ------------------------------------------------------------------ saved EVA Admin sign-in (this Windows user only)
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows per-user encryption (DPAPI). The saved sign-in cannot be read by another Windows user."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError('Windows could not protect the saved EVA Admin sign-in')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def save_admin_session(cookies: list) -> None:
    raw = json.dumps({'cookies': cookies, 'saved_at': time.time()}, separators=(',', ':')).encode('utf-8')
    if os.name == 'nt':
        blob = b'EVA1' + _dpapi(raw, True)
    elif os.getenv('EVA_ALLOW_INSECURE_STORE') == '1':  # automated tests only
        blob = b'EVA0' + raw
    else:
        return  # never write browser cookies unprotected
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SESSION_FILE.with_suffix('.tmp')
    temp.write_bytes(blob)
    os.replace(temp, SESSION_FILE)  # atomic: a crash cannot leave half a file


def load_admin_session() -> dict | None:
    try:
        blob = SESSION_FILE.read_bytes()
        if blob.startswith(b'EVA1') and os.name == 'nt':
            return json.loads(_dpapi(blob[4:], False).decode('utf-8'))
        if blob.startswith(b'EVA0') and os.getenv('EVA_ALLOW_INSECURE_STORE') == '1':
            return json.loads(blob[4:].decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        pass
    return None  # missing, from another Windows user, or damaged: treated as signed out


def forget_admin_session() -> None:
    SESSION_FILE.unlink(missing_ok=True)


def _restore_session(ctx) -> None:
    """Chrome drops 'session' cookies when it closes. Put back only the ones the profile no longer has."""
    saved = load_admin_session()
    if not saved or not saved.get('cookies'):
        return
    try:
        have = {(c['name'], c.get('domain'), c.get('path')) for c in ctx.cookies()}
        horizon = time.time() + 14 * 86400
        restore = []
        for cookie in saved['cookies']:
            if (cookie.get('name'), cookie.get('domain'), cookie.get('path')) in have:
                continue
            cookie = dict(cookie)
            if not cookie.get('expires') or cookie['expires'] < 0:
                cookie['expires'] = horizon
            restore.append(cookie)
        if restore:
            ctx.add_cookies(restore)
            LOG.info('Restored %s saved EVA Admin cookies', len(restore))
    except Exception as exc:
        LOG.warning('Could not restore the saved EVA Admin sign-in: %s', type(exc).__name__)


def _save_session(ctx, force: bool = False) -> None:
    """Remember the current sign-in (cookies, including refreshed ones). Throttled; never raises."""
    global _SESSION_SAVED_AT
    if not force and time.time() - _SESSION_SAVED_AT < 120:
        return
    try:
        cookies = [c for c in ctx.cookies() if any(h in c.get('domain', '') for h in ('eva.gg', '127.0.0.1', 'localhost'))]
        if cookies:
            save_admin_session(cookies)
            _SESSION_SAVED_AT = time.time()
    except Exception as exc:
        LOG.warning('Could not save the EVA Admin sign-in: %s', type(exc).__name__)


def admin_session_saved() -> bool:
    return load_admin_session() is not None


# ------------------------------------------------------------------ venue settings (Settings → EVA Admin)
def _allowed_venues() -> list[str]:
    value = settings().get('venue_names')
    names = value if isinstance(value, list) else str(value or '').split(',')
    names = [n.strip().casefold() for n in names if n and n.strip()]
    return names or list(DEFAULT_VENUES)


def _arena_numbers() -> list[str]:
    value = settings().get('arena_numbers')
    numbers = value if isinstance(value, list) else str(value or '').split(',')
    numbers = [str(n).strip() for n in numbers if str(n).strip().isdigit()]
    return numbers or ['1']

def _brave_paths():
    roots = [os.getenv('ProgramFiles', ''), os.getenv('ProgramFiles(x86)', ''), os.getenv('LOCALAPPDATA', '')]
    return [Path(r) / 'BraveSoftware' / 'Brave-Browser' / 'Application' / 'brave.exe' for r in roots if r]


def find_browser():
    """Which browser EVA uses for EVA Admin: (name, executable or None, profile folder).
    Brave is used when it is installed; otherwise Google Chrome. Settings can force either."""
    config = settings()
    custom = str(config.get('browser_path') or '').strip()
    if custom and Path(custom).is_file():
        brave = 'brave' in custom.casefold()
        return ('Brave' if brave else 'Browser'), custom, (BRAVE_PROFILE_DIR if brave else PROFILE_DIR)
    preference = str(config.get('browser') or 'auto').casefold()
    brave_exe = next((p for p in _brave_paths() if p.is_file()), None)
    if preference in ('auto', 'brave') and brave_exe:
        return 'Brave', str(brave_exe), BRAVE_PROFILE_DIR
    return 'Chrome', None, PROFILE_DIR


def close_browser():
    """Close EVA's browser (run on the browser worker thread), e.g. after the browser setting changed."""
    global _CONTEXT
    with _LOCK:
        _stop_brave()
        if _CONTEXT is not None:
            try:
                _CONTEXT.close()
            except Exception:
                pass
            _CONTEXT = None
        return {'ok': True}


def _forget_context(*_):
    global _CONTEXT, _MODE
    _CONTEXT = None
    _MODE = None

def _window(page, state):
    try:
        session = page.context.new_cdp_session(page)
        try:
            window = session.send('Browser.getWindowForTarget')
            session.send('Browser.setWindowBounds', {'windowId':window['windowId'], 'bounds':{'windowState':state}})
        finally:
            session.detach()
    except Exception:
        pass


class EvaPortalError(RuntimeError):
    pass


class LayoutChanged(EvaPortalError):
    """EVA Admin no longer looks the way EVA expects. Matching pauses (recording and delivery carry on); recipients can be entered by hand."""



def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _cdp_alive(port) -> bool:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/json/version', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _read_cdp_state() -> dict:
    try:
        return json.loads(CDP_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _close_running(pw, port) -> None:
    """Ask a Brave window EVA started earlier (even in a previous EVA run) to close, and wait until it has."""
    try:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{int(port)}', timeout=10_000)
        browser.new_browser_cdp_session().send('Browser.close')
    except Exception:
        pass
    for _ in range(40):
        if not _cdp_alive(port):
            break
        time.sleep(0.2)


_JOB = {'handle': None}


def _close_with_eva(proc) -> None:
    """Windows: put the browser in a job that is closed when EVA exits, so a hidden Brave can never be left running
    invisibly after EVA is closed (or killed). Best effort: any problem just means no automatic close."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]

        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('Io', Counters), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]
        if _JOB['handle'] is None:
            job = kernel32.CreateJobObjectW(None, None)
            info = Extended()
            info.Basic.LimitFlags = 0x2000             # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _JOB['handle'] = job                       # kept for the life of EVA; Windows closes it when EVA exits
        kernel32.AssignProcessToJobObject(_JOB['handle'], int(proc._handle))
    except Exception as exc:
        LOG.warning('Could not tie the browser to EVA: %s', type(exc).__name__)


def _start_brave(exe: str, profile, visible: bool = False, pw=None) -> int:
    """Start Brave like a normal program and return its debugging port.
    hidden (default): --headless=new, so there is no window and no taskbar icon at all;
    visible: an ordinary window, only used while the operator signs in.
    EVA Admin's address is given to Brave itself, so it never sits on about:blank."""
    global _BROWSER_PROC
    state = _read_cdp_state()
    if state.get('profile') == str(profile) and state.get('exe') == exe and _cdp_alive(state.get('port', 0)):
        if bool(state.get('visible')) == visible:
            return int(state['port'])                  # the same kind of window from an earlier EVA run: reuse it
        if pw is not None:
            _close_running(pw, state['port'])          # same profile, other mode: close that one first
    port = _free_port()
    command = [exe, f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1', f'--user-data-dir={profile}',
               '--no-first-run', '--no-default-browser-check', '--disable-features=Translate']
    if not visible:
        command += ['--headless=new', '--window-size=1600,1000']
    command.append(BASE_URL + '/bookings')
    _LAST_LAUNCH.update(args=command, visible=visible)
    LOG.info('Starting %s for EVA Admin (%s) on debugging port %s', Path(exe).name, 'sign-in window' if visible else 'hidden', port)
    _BROWSER_PROC = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    _close_with_eva(_BROWSER_PROC)
    deadline = time.monotonic() + BRAVE_START_TIMEOUT
    while time.monotonic() < deadline:
        if _cdp_alive(port):
            CDP_FILE.parent.mkdir(parents=True, exist_ok=True)
            CDP_FILE.write_text(json.dumps({'port': port, 'profile': str(profile), 'exe': exe, 'visible': visible}), encoding='utf-8')
            return port
        if _BROWSER_PROC.poll() is not None:
            raise EvaPortalError('Brave closed straight after starting. If another Brave window is using EVA\'s profile, '
                                 'close it (or use Sign out of EVA Admin in Settings), then try again.')
        time.sleep(0.4)
    try:
        _BROWSER_PROC.terminate()
    except Exception:
        pass
    raise EvaPortalError(f'Brave did not become ready within {BRAVE_START_TIMEOUT} seconds.')


def _choose_page(ctx):
    """The tab that shows EVA Admin; Brave's own start tabs are left alone."""
    pages = [p for p in ctx.pages if not p.is_closed()]
    host = urlparse(BASE_URL).hostname or ''
    for page in pages:
        if (host and host in page.url) or 'auth.eva.gg' in page.url:
            return page
    for page in pages:
        if not page.url.startswith(('brave://', 'chrome://', 'edge://')):
            return page
    return ctx.new_page() if not pages else pages[0]


def _ensure_loaded(ctx):
    """A freshly opened browser must not stay on about:blank: give its own start page a moment, then navigate it."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        pages = [p for p in ctx.pages if not p.is_closed()]
        if any(p.url not in ('', 'about:blank') for p in pages):
            return
        if pages:
            pages[0].wait_for_timeout(200)
        else:
            time.sleep(0.2)
    page = _choose_page(ctx)
    page.goto(BASE_URL + '/bookings', wait_until='domcontentloaded', timeout=45_000)


def _open_context(pw, name, exe, profile, visible=False):
    if name == 'Brave' or (exe and name != 'Chrome'):
        port = _start_brave(exe, profile, visible, pw)
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=20_000)
        ctx = browser.contexts[0]
        browser.on('disconnected', _forget_context)
        return ctx
    launch = dict(headless=not visible, args=["--no-first-run", "--disable-features=Translate"], no_viewport=visible)
    if not visible:
        launch['viewport'] = {'width': 1600, 'height': 1000}
    if exe:
        launch['executable_path'] = exe
    else:
        launch['channel'] = 'chrome'
    return pw.chromium.launch_persistent_context(str(profile), **launch)


def _page(interactive: bool = True, visible: bool = False):
    """The EVA Admin tab. By default the connection is HIDDEN (no window at all). visible=True is used only while the
    operator signs in. interactive=False is the background booking sync: after a failed start it must not keep
    trying by itself, so it waits LAUNCH_COOLDOWN seconds (a click in Settings always tries again)."""
    global _CONTEXT, _LAUNCH_FAILED_AT, _MODE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EvaPortalError("Browser support is missing. Run START_EVA_MANAGER.bat again.") from exc
    if _CONTEXT is not None and _CONTEXT.browser is not None and not _CONTEXT.browser.is_connected():
        _CONTEXT = None
        _MODE = None
    if _CONTEXT is not None and _MODE == 'visible' and not visible:
        raise EvaPortalError('Finish signing in to EVA Admin in the browser window. It closes by itself when you are done.')
    if _CONTEXT is not None and _MODE == 'hidden' and visible:
        _stop_brave()                                  # switch from the hidden connection to the sign-in window
        try:
            _CONTEXT.close()
        except Exception:
            pass
        _CONTEXT = None
        _MODE = None
    if _CONTEXT is None:
        if not interactive and _LAUNCH_FAILED_AT and time.monotonic() - _LAUNCH_FAILED_AT < LAUNCH_COOLDOWN:
            raise EvaPortalError('The EVA Admin browser could not be started a moment ago. Press Sign in to EVA Admin in Settings to try again.')
        browser_name, browser_exe, profile = find_browser()
        profile.mkdir(parents=True, exist_ok=True)
        if not hasattr(_page, "playwright"):
            _page.playwright = sync_playwright().start()
        try:
            try:
                _CONTEXT = _open_context(_page.playwright, browser_name, browser_exe, profile, visible)
            except Exception as first:
                # Automatic mode: if Brave cannot be started, use Google Chrome rather than leave EVA without Admin.
                if browser_name == 'Brave' and str(settings().get('browser') or 'auto').casefold() == 'auto' \
                        and not str(settings().get('browser_path') or '').strip():
                    LOG.warning('Brave could not be started (%s); trying Google Chrome', type(first).__name__)
                    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                    try:
                        _CONTEXT = _open_context(_page.playwright, 'Chrome', None, PROFILE_DIR, visible)
                    except Exception:
                        raise first
                else:
                    raise
            _CONTEXT.on('close', _forget_context)
            _MODE = 'visible' if visible else 'hidden'
            _restore_session(_CONTEXT)
            _ensure_loaded(_CONTEXT)
            _LAUNCH_FAILED_AT = 0.0
        except EvaPortalError:
            _LAUNCH_FAILED_AT = time.monotonic()
            raise
        except Exception as exc:
            _LAUNCH_FAILED_AT = time.monotonic()
            raise EvaPortalError(
                "Brave or Google Chrome is required. Install one of them, then open EVA again. "
                f"Details: {exc}"
            ) from exc
    pages = [p for p in _CONTEXT.pages if not p.is_closed()]
    if _CONTEXT.pages and not pages:
        _CONTEXT = None
        return _page()
    return _choose_page(_CONTEXT)


def _stop_brave() -> None:
    """Close the Brave window EVA started (if any) and forget it."""
    global _BROWSER_PROC
    try:
        if _CONTEXT is not None and _CONTEXT.browser is not None:
            _CONTEXT.browser.new_browser_cdp_session().send('Browser.close')
    except Exception:
        pass
    proc, _BROWSER_PROC = _BROWSER_PROC, None
    if proc is not None:
        try:
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
    CDP_FILE.unlink(missing_ok=True)


def _finish_signin():
    """While the sign-in window is open: once EVA Admin is signed in, save the login, close the window and continue
    hidden. Returns True when done, False while still signing in, None when no sign-in window is open."""
    global _CONTEXT, _MODE
    ctx = _CONTEXT
    if ctx is None or _MODE != 'visible':
        return None
    page = _choose_page(ctx)
    if _is_login_page(page.url) or page.url in ('', 'about:blank'):
        return False
    page.wait_for_timeout(800)                         # let the sign-in finish writing its cookies
    if _is_login_page(page.url):
        return False
    _save_session(ctx, force=True)
    _stop_brave()
    try:
        ctx.close()
    except Exception:
        pass
    _CONTEXT, _MODE = None, None
    _page(interactive=True)                            # starts the hidden connection with the saved login
    return True


def signin_progress() -> dict:
    """Polled while the sign-in window is open (run on the browser worker thread)."""
    with _LOCK:
        done = _finish_signin()
        return {'state': 'idle' if done is None else 'done' if done else 'waiting'}


def sign_out_admin() -> dict:
    """Close EVA's browser, delete the saved sign-in and the browser profile. Run on the browser worker thread."""
    global _CONTEXT, _SESSION_SAVED_AT
    with _LOCK:
        _stop_brave()
        if _CONTEXT is not None:
            try:
                _CONTEXT.close()
            except Exception:
                pass
            _CONTEXT = None
        forget_admin_session()
        _SESSION_SAVED_AT = 0.0
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        shutil.rmtree(BRAVE_PROFILE_DIR, ignore_errors=True)
        return {'ok': True, 'message': 'Signed out of EVA Admin. The saved sign-in was removed from this PC.'}


def open_eva() -> dict:
    with _LOCK:
        page = _page(visible=True)                     # the one moment a browser window is shown: signing in
        _window(page, 'normal')
        page.bring_to_front()
        page.goto(BASE_URL + "/planning", wait_until="domcontentloaded", timeout=45_000)
        time.sleep(1)
        LOG.info('EVA Admin window opened: %s', page.url)
        return {"ok": True, "url": page.url, "title": page.title(), "profile_dir": str(PROFILE_DIR),
                "login_required": _is_login_page(page.url)}


def _is_login_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname != 'admin.eva.gg' or any(x in parsed.path.casefold() for x in ("login", "auth", "signin"))


def _tables(page) -> list[dict]:
    try:
        return page.locator("table").evaluate_all("""tables => tables.map(t => {
          const rows=[...t.querySelectorAll('tr')].map(r => ({
            cells:[...r.querySelectorAll('th,td')].map(c => (c.innerText||'').trim()),
            booking:r.getAttribute('data-booking'),
            links:[...r.querySelectorAll('a[href]')].map(a => ({text:(a.innerText||'').trim(),href:a.href})),
            buttons:[...r.querySelectorAll('button')].map(b => (b.innerText||'').trim())
          }));
          return {rows};
        })""")
    except Exception:
        return []


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _column(headers: list[str], choices: tuple[str, ...]) -> int | None:
    h = [_norm(x) for x in headers]
    for i, name in enumerate(h):
        if any(choice in name for choice in choices):
            return i
    return None


def _parse_date(value: str) -> str | None:
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
                "%m/%d/%Y", "%m-%d-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    found = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", v)
    if found:
        day, month, year = map(int, found.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _parse_time(value: str) -> str | None:
    v = value.strip().replace(".", ":")
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(v.upper(), fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return None


def _booking_rows(page) -> list[dict]:
    result = []
    for table in _tables(page):
        rows = table.get("rows", [])
        if len(rows) < 2:
            continue
        headers = rows[0]["cells"]
        id_col = _column(headers, ("bookingid", "bookingnumber", "reservationid"))
        date_col = _column(headers, ("sessiondate", "bookingdate", "date"))
        start_col = next((i for i, h in enumerate(headers) if any(x in _norm(h) for x in ("sessionstart", "starttime", "start")) and not any(x in _norm(h) for x in ("checkin", "arrival"))), None)
        end_col = next((i for i, h in enumerate(headers) if any(x in _norm(h) for x in ("sessionend", "endtime", "end")) and not any(x in _norm(h) for x in ("checkout",))), None)
        arena_col = _column(headers, ("arena", "location"))
        status_col = _column(headers, ("status", "state"))
        game_col = _column(headers, ("game", "experience"))
        branch_col = _column(headers, ('branch', 'center', 'centre'))
        session_col = _column(headers, ('officialsessionid', 'sessionid'))
        for row in rows[1:]:
            cells = row["cells"]
            if not cells:
                continue
            def val(col, cells=cells):
                return cells[col].strip() if col is not None and col < len(cells) else ""
            session_date = _parse_date(val(date_col))
            start = _parse_time(val(start_col))
            end = _parse_time(val(end_col))
            booking_id = val(id_col)
            arena = val(arena_col)
            if not (session_date and start and end and booking_id and arena):
                continue
            href = next((link["href"] for link in row.get("links", [])
                         if re.search(r"booking|reservation|detail", link.get("href", ""), re.I)), None)
            if not href:
                href = next((link["href"] for link in row.get("links", [])), None)
            arena_key = _norm(arena)
            # Arena 1 alone is not a globally unique branch identifier.
            branch = val(branch_col).casefold()
            if branch and 'bahrain' not in branch:
                continue
            arena_id = '81' if arena_key == '81' or ('bahrain' in branch and arena_key in {'arena1', '1'}) else arena
            result.append({"booking_id": booking_id, "branch": "Bahrain", "arena_id": arena_id,
                           "session_date": session_date, "start_time": start, "end_time": end,
                           "status": val(status_col) or "active", "game": val(game_col),
                           "official_session_id": val(session_col) or None,
                           "detail_url": href, "row_text": " | ".join(cells)})
    return result


def _session_times(value):
    match = re.fullmatch(r'\s*(.+?)\s*\(\s*To\s+(.+?)\s*\)\s*', value, re.I)
    return (_parse_time(match[1]), _parse_time(match[2])) if match else (None, None)


def _scope_name(page) -> str:
    """The venue EVA Admin is currently scoped to: the 'Scope' menu button, or a venue drop-down."""
    allowed = _allowed_venues()
    try:
        selected = page.locator('select').evaluate_all("els=>els.map(e=>e.selectedOptions[0]?.textContent?.trim()||'')")
    except Exception:
        selected = []
    for text in selected:
        if any(v in text.casefold() for v in allowed):
            return text
    try:
        text = page.locator('button', has_text=re.compile(r'^\s*Scope')).first.inner_text(timeout=4000)
        found = re.search(r'Scope\s*:?\s*(.+)', re.sub(r'\s+', ' ', text).strip(), re.I)
        if found:
            return found.group(1).strip()
    except Exception:
        pass
    return ''


def _require_bahrain(page, strict: bool = True):
    """Only the configured venues (default Bahrain and Marassi) are accepted; anything else blocks sync."""
    scope = _scope_name(page)
    if not scope:
        if strict:
            raise EvaPortalError('Could not read which venue EVA Admin is set to. Open EVA Admin and select your venue.')
        return ''
    if not any(v in scope.casefold() for v in _allowed_venues()):
        raise EvaPortalError(f'EVA Admin is set to "{scope}", which is not one of the allowed venues '
                             f'({", ".join(v.title() for v in _allowed_venues())}). Select your venue and retry.')
    if settings().get('venue_label') != scope:
        try:
            save_settings(venue_label=scope)
        except Exception:
            pass
    return scope


def _live_rows(tables):
    """English Admin uses month/day dates and a single Session column."""
    result = []
    looked, matched = 0, 0
    for table in tables:
        rows = table.get('rows', [])
        if not rows:
            continue
        headers = [_norm(x) for x in rows[0]['cells']]
        if len(headers) >= 5:
            looked += 1
        if not {'bookingorder','date','session','game','status'}.issubset(headers):
            continue
        matched += 1
        for row in rows[1:]:
            cells = row['cells']
            if len(cells) != len(headers) or not str(row.get('booking') or '').isdigit():
                continue
            values = dict(zip(headers, cells))
            try:
                day = datetime.strptime(values['date'], '%m/%d/%Y').date().isoformat()
            except ValueError:
                raise LayoutChanged('EVA Admin changed (date format). Matching is paused. Select English, or enter the recipients manually.') from None
            start, end = _session_times(values['session'])
            if not start or not end:
                raise LayoutChanged('EVA Admin changed (play times unreadable). Matching is paused. Enter the recipients manually.')
            owner_link = next((x['href'] for x in row.get('links', []) if '/users/view/' in x['href']), '')
            owner = unquote(urlparse(owner_link).path.split('/users/view/')[-1]) if owner_link else None
            result.append(dict(booking_id=row['booking'], branch='Bahrain', arena_id='81',
                               session_date=day, start_time=start, end_time=end,
                               status=values['status'], game=values['game'], owner_user_id=owner,
                               detail_url=BASE_URL+'/bookings/'+row['booking'], order=values['bookingorder'],
                               user=values.get('user',''), players=values.get('players',''),
                               convocation=values.get('convocationtime',''), payment=values.get('payment','')))
    if looked and not matched:
        raise LayoutChanged('EVA Admin changed (booking columns are different). Matching is paused. Select English, or enter the recipients manually.')
    return result


def _wait_bookings(page):
    try:
        page.locator('table').first.wait_for(state='visible', timeout=30_000)
        page.locator('select:disabled').first.wait_for(state='hidden', timeout=30_000)
    except Exception as exc:
        if _is_login_page(page.url):
            raise
        raise LayoutChanged('EVA Admin changed (no booking table appeared). Matching is paused. Enter the recipients manually.') from exc


def verify_eva():
    """Verify the signed-in profile and layout without importing customer contacts."""
    with _LOCK:
        if _finish_signin() is False:
            return dict(ok=False, login_required=True, message='Finish signing in to EVA Admin in the browser window first.')
        page = _page()
        page.goto(BASE_URL+'/bookings', wait_until='domcontentloaded', timeout=45_000)
        if _is_login_page(page.url):
            return dict(ok=False, login_required=True, message='Sign in to EVA Admin from Settings first.')
        _wait_bookings(page)
        _require_bahrain(page)
        tables = _tables(page)
        supported = any({'bookingorder','date','session','game','status'}.issubset({_norm(x) for x in t['rows'][0]['cells']}) for t in tables if t.get('rows'))
        if not supported:
            raise LayoutChanged('EVA Admin changed (booking table). Matching is paused. Select English, or enter the recipients manually.')
        scope = _require_bahrain(page)
        _save_session(page.context, force=True)
        return dict(ok=True, login_required=False, branch=scope or 'Bahrain',
                    message=f'EVA Admin connected · {scope or "Bahrain"}. Booking layout verified; recipients are read only for the selected play session.')


def _live_candidates(page, recording):
    _require_bahrain(page)
    target = date.fromisoformat(recording['session_date'])
    today = datetime.now(BAHRAIN).date()
    preset = 'Today' if target == today else 'Last 30 days' if 0 <= (today-target).days <= 30 else 'All sessions'
    page.locator('#date-range-picker').click()
    page.get_by_role('button', name=preset, exact=True).click()
    _wait_bookings(page)
    found, seen = {}, set()
    for _ in range(100):
        rows = _live_rows(_tables(page))
        fingerprint = tuple(x['booking_id'] for x in rows)
        if fingerprint in seen:
            raise EvaPortalError('Booking pagination did not advance. Synchronization is blocked to avoid missing recipients.')
        seen.add(fingerprint)
        for row in rows:
            if row['session_date'] == recording['session_date']:
                found[row['booking_id']] = row
        nxt = page.get_by_role('button', name='»', exact=True)
        if not nxt.count() or not nxt.is_enabled() or nxt.evaluate("e=>!!e.closest('.disabled') || e.getAttribute('aria-disabled')==='true'"):
            return list(found.values())
        nxt.click()
        page.wait_for_function("ids => JSON.stringify([...document.querySelectorAll('tr[data-booking]')].map(r=>r.dataset.booking)) !== JSON.stringify(ids)", arg=list(fingerprint), timeout=30_000)
        _wait_bookings(page)
    raise EvaPortalError('Too many booking pages. Narrow the EVA Admin usage dates before synchronizing.')


def day_bookings(session_date):
    with _LOCK:
        date.fromisoformat(session_date)
        page = _page(interactive=False)
        page.goto(BASE_URL+'/bookings', wait_until='domcontentloaded', timeout=45_000)
        if _is_login_page(page.url):
            return dict(ok=False, login_required=True, sessions=[], message='Sign in using Open EVA Admin in EVA Settings, then load bookings again.')
        _wait_bookings(page)
        rows = [r for r in _live_candidates(page, {'session_date':session_date}) if not _is_cancelled(r.get('status'))]   # cancelled bookings are not shown anywhere
        sessions = group_sessions(rows, session_date)
        _save_session(page.context)
        _window(page, 'minimized')
        return dict(ok=True, login_required=False, sessions=sessions, bookings=[{k:v for k,v in r.items() if k!='detail_url'} for r in rows],
                    message=f'{len(sessions)} game sessions found for {session_date}. Select a session to verify its linked recipients.')


def session_recipients(session):
    if str(session.get('branch','Bahrain')).casefold() != 'bahrain' or str(session.get('arena_id','81')) != '81':
        raise EvaPortalError('Only Bahrain Arena 1 is supported.')
    date.fromisoformat(session['session_date'])
    datetime.strptime(session['start_time'], '%H:%M:%S')
    datetime.strptime(session['end_time'], '%H:%M:%S')
    with _LOCK:
        page = _page()
        page.goto(BASE_URL+'/bookings', wait_until='domcontentloaded', timeout=45_000)
        if _is_login_page(page.url):
            return dict(ok=False, login_required=True, bookings=[], users=[], message='EVA Admin sign-in expired. Open EVA Admin from Settings.')
        _wait_bookings(page)
        return _pull_live(page, session)


def _live_detail(page, row):
    page.goto(row['detail_url'], wait_until='domcontentloaded', timeout=45_000)
    # The page title is "Order#…"; the booking itself is the row that links to /bookings/<id>.
    page.locator('a[href="/bookings/'+row['booking_id']+'"]').first.wait_for(state='visible', timeout=30_000)
    if _is_login_page(page.url):
        raise EvaPortalError('EVA Admin login expired.')
    if page.locator('button').filter(has_text=re.compile(r'^\s*Scope')).count():   # the venue button is normally only on list pages: no waiting when absent
        _require_bahrain(page, strict=False)
    labels = re.findall(r'(?m)^\s*Arena\s*\n\s*(\d+)\s*$', page.locator('body').inner_text())
    if not labels or any(label not in _arena_numbers() for label in labels):
        raise EvaPortalError('Booking does not verify the configured arena (' + ', '.join(_arena_numbers()) +
                             '). Sending is blocked.')
    detail = page.locator('tr').filter(has=page.locator('a[href="/bookings/'+row['booking_id']+'"]'))
    cells = detail.locator('td').all_inner_texts()
    if len(cells) < 7:
        raise EvaPortalError('Could not verify the selected booking details.')
    day = datetime.strptime(cells[2].strip(), '%m/%d/%Y').date().isoformat()
    start, end = _session_times(cells[3])
    if (day,start,end,cells[5].strip()) != (row['session_date'],row['start_time'],row['end_time'],row['game']):
        raise EvaPortalError('Booking details changed. Review the actual session before sending.')
    button = page.get_by_role('button', name=re.compile(r'^View\s+\d+\W\d+\s+attendees$', re.I))  # no '/' in the pattern: Playwright cannot parse it in a selector
    button.wait_for(timeout=30_000)
    counts = re.search(r'(\d+)/(\d+)', button.inner_text())
    button.click()
    dialog = page.locator('.modal.show').filter(has_text=re.compile(r'associate a player account', re.I)).first
    dialog.wait_for(state='visible', timeout=15_000)
    links = dialog.locator('table a[href*="/users/view/"]').evaluate_all("es=>es.map(e=>e.href)")
    users = list(dict.fromkeys(unquote(urlparse(link).path.split('/users/view/')[-1]) for link in links))
    if not counts or len(users) != int(counts[1]):
        raise EvaPortalError('The attendee list did not load completely. Retry before sending.')
    linked, total = int(counts[1]), int(counts[2])
    if settings().get('require_all_attendees') is True and (linked == 0 or linked != total):
        raise EvaPortalError('Not every player has a linked account. Review the attendee list before sending.')
    booking = {k:v for k,v in row.items() if k != 'detail_url'}
    booking['status'] = cells[1].strip()
    booking['attendee_user_ids'] = users
    booking['attendees_linked'], booking['attendees_total'] = linked, total
    return booking


def _live_user(page, uid):
    page.goto(BASE_URL+'/users/view/'+quote(uid, safe=''), wait_until='domcontentloaded', timeout=45_000)
    if _is_login_page(page.url):
        raise EvaPortalError('EVA Admin login expired.')
    page.locator('input[name="email"]').wait_for(state='attached', timeout=30_000)
    page.wait_for_function("()=>document.querySelector('input[name=firstName]')?.value || document.querySelector('input[name=email]')?.value", timeout=30_000)
    value = lambda name: page.locator('input[name="'+name+'"]').input_value().strip()
    return dict(user_id=uid, username=uid, name=(value('firstName')+' '+value('lastName')).strip(), email=value('email'))


def _live_users(page, uids, width=4):
    """Player account pages loaded side by side in extra tabs (the browser loads them at the same time)."""
    users = {}
    todo = [u for u in uids]
    if len(todo) <= 1:
        return {u: _live_user(page, u) for u in todo}
    ctx = page.context
    value = lambda pg, name: pg.locator('input[name="'+name+'"]').input_value().strip()
    for i in range(0, len(todo), width):
        chunk, tabs = todo[i:i+width], []
        try:
            for uid in chunk:
                tab = ctx.new_page()
                tabs.append((uid, tab))
                tab.goto(BASE_URL+'/users/view/'+quote(uid, safe=''), wait_until='commit', timeout=45_000)
            for uid, tab in tabs:
                if _is_login_page(tab.url):
                    raise EvaPortalError('EVA Admin login expired.')
                tab.locator('input[name="email"]').wait_for(state='attached', timeout=30_000)
                tab.wait_for_function("()=>document.querySelector('input[name=firstName]')?.value || document.querySelector('input[name=email]')?.value", timeout=30_000)
                users[uid] = dict(user_id=uid, username=uid, name=(value(tab, 'firstName')+' '+value(tab, 'lastName')).strip(), email=value(tab, 'email'))
        finally:
            for _, tab in tabs:
                try:
                    tab.close()
                except Exception:
                    pass
    return users


def _pull_live(page, recording, rows=None):
    t0 = time.monotonic()
    cached = rows is not None
    if rows is None:
        rows = _live_candidates(page, recording)
    t_list = time.monotonic() - t0
    match = match_bookings(recording, rows)
    if match['status'] != 'matched':
        return dict(ok=True, match=match['status'], bookings=[], users=[], message='No unique actual play session matches these times in Bahrain.')
    selected = match['bookings']
    bookings, wanted = [], []
    t1 = time.monotonic()
    for row in selected:
        booking = _live_detail(page,row)
        bookings.append(booking)
        for uid in dict.fromkeys(booking['attendee_user_ids']+([booking['owner_user_id']] if booking['owner_user_id'] else [])):
            if uid not in wanted:
                wanted.append(uid)
    t_detail = time.monotonic() - t1
    t2 = time.monotonic()
    users = _live_users(page, wanted)
    LOG.info('EVA lookup timing: booking list %.1fs (%s), booking pages %.1fs (%s), player accounts %.1fs (%s)', t_list,
             'reused' if cached else 'read', t_detail, len(selected), time.monotonic() - t2, len(wanted))
    final = match_bookings(recording,bookings)
    if final['status'] != 'matched' or len(final['bookings']) != len(bookings):
        raise EvaPortalError('Booking status or play session changed. Synchronization is blocked.')
    notes = []
    for booking in bookings:
        linked, total = booking.get('attendees_linked'), booking.get('attendees_total')
        if total and linked is not None and linked < total:
            notes.append(f'Booking {booking["booking_id"]}: {linked} of {total} players have an EVA account. '
                         'Only those players and the booking owner can be emailed; add other emails by hand if needed.')
    _save_session(page.context)
    return dict(ok=True, login_required=False, match='matched', bookings=bookings, users=list(users.values()),
                scoped_user_lookup=True, notes=notes,
                message=f'Verified {len(bookings)} matching bookings and their linked player accounts.')


def _detail_fields(page) -> dict[str, str]:
    fields = {}
    for table in _tables(page):
        for row in table.get("rows", []):
            cells = row.get("cells", [])
            if len(cells) == 2:
                fields[_norm(cells[0])] = cells[1].strip()
    try:
        items = page.locator("dl").evaluate_all("""els=>els.flatMap(e=>{const a=[...e.querySelectorAll('dt')],b=[...e.querySelectorAll('dd')];return a.map((x,i)=>[x.innerText,b[i]?.innerText||''])})""")
        for key, value in items:
            fields[_norm(key)] = str(value).strip()
    except Exception:
        pass
    return fields


def _find_field(fields: dict[str, str], terms: tuple[str, ...]) -> str | None:
    for key, value in fields.items():
        if any(term in key for term in terms) and value:
            return value
    return None


def _attendee_ids(page) -> list[str]:
    ids = []
    for table in _tables(page):
        rows = table.get("rows", [])
        if len(rows) < 2:
            continue
        headers = rows[0]["cells"]
        col = _column(headers, ("userid", "playerid", "accountid", "attendeeid"))
        if col is None:
            continue
        for row in rows[1:]:
            cells = row["cells"]
            if col < len(cells) and cells[col].strip():
                ids.append(cells[col].strip())
    return list(dict.fromkeys(ids))


def _lookup_user(page, user_id: str) -> dict:
    page.goto(BASE_URL + "/users/list", wait_until="domcontentloaded", timeout=45_000)
    if _is_login_page(page.url):
        raise EvaPortalError('EVA Admin sign-in expired. Open EVA Admin on the Streaming PC and sign in again.')
    search = None
    for field in page.locator("input").all():
        try:
            attrs = " ".join(str(field.get_attribute(k) or "") for k in ("placeholder", "aria-label", "name", "type"))
            if any(word in attrs.casefold() for word in ("search", "filter", "user")):
                search = field
                break
        except Exception:
            continue
    if search is None:
        return {"user_id": user_id, "username": "", "name": "", "email": ""}
    search.fill(user_id)
    search.press("Enter")
    time.sleep(.6)
    for table in _tables(page):
        rows = table.get("rows", [])
        if len(rows) < 2:
            continue
        headers = rows[0]["cells"]
        id_col = _column(headers, ("userid", "accountid", "id"))
        email_col = _column(headers, ("email", "e-mail"))
        name_col = _column(headers, ("fullname", "name"))
        username_col = _column(headers, ("username", "user"))
        for row in rows[1:]:
            cells = row["cells"]
            if id_col is None or id_col >= len(cells) or cells[id_col].strip() != user_id:
                continue
            def val(col, cells=cells): return cells[col].strip() if col is not None and col < len(cells) else ""
            return {"user_id": user_id, "username": val(username_col), "name": val(name_col), "email": val(email_col)}
    return {"user_id": user_id, "username": "", "name": "", "email": ""}


def pull_recording_session(recording: dict, rows: list | None = None) -> dict:
    """Read matching bookings only, then read only attendee/owner account IDs from them.
    `rows`: the day's bookings the background sync read moments ago; with them the booking list is not loaded again."""
    with _LOCK:
        page = _page()
        if rows:
            rows = [dict(r, detail_url=r.get('detail_url') or BASE_URL + '/bookings/' + str(r['booking_id'])) for r in rows
                    if r.get('session_date') == recording.get('session_date')]
            if rows:
                return _pull_live(page, recording, rows)
        page.goto(BASE_URL + "/bookings", wait_until="domcontentloaded", timeout=45_000)
        time.sleep(1)
        if _is_login_page(page.url):
            return {"ok": False, "login_required": True, "message": "سجّل الدخول إلى EVA في نافذة المتصفح المفتوحة، ثم أعد المزامنة."}
        _wait_bookings(page)
        if page.locator('tr[data-booking]').count():
            return _pull_live(page, recording)
        candidate_rows = _booking_rows(page)
        candidates = [{k: v for k, v in row.items() if k not in {"detail_url", "row_text"}}
                      for row in candidate_rows]
        match = match_bookings(recording, candidates)
        if match["status"] != "matched":
            return {"ok": True, "login_required": False, "match": match["status"], "bookings": [], "users": [],
                    "message": f"ما لقينا تطابق فريد. الحالة: {match['status']}. تأكد من التاريخ والساحة ووقت اللعب."}
        selected_ids = {str(x["booking_id"]) for x in match["bookings"]}
        selected_rows = [row for row in candidate_rows if str(row["booking_id"]) in selected_ids]
        imported_bookings = []
        users_by_id = {}
        for row in selected_rows:
            if not row.get("detail_url"):
                continue
            detail_url = urljoin(BASE_URL + "/bookings", row["detail_url"])
            if urlparse(detail_url).netloc != urlparse(BASE_URL).netloc:
                continue
            page.goto(detail_url, wait_until="domcontentloaded", timeout=45_000)
            time.sleep(.4)
            if _is_login_page(page.url):
                return {"ok": False, "login_required": True, "message": "انتهت جلسة EVA. سجّل الدخول مرة ثانية ثم أعد المزامنة."}
            fields = _detail_fields(page)
            booking = {k: v for k, v in row.items() if k not in {"detail_url", "row_text", "game"}}
            booking["game"] = _find_field(fields, ("game", "experience")) or row.get("game", "")
            booking["status"] = _find_field(fields, ("status", "state")) or row.get("status", "active")
            detail_date = _find_field(fields, ("sessiondate", "bookingdate"))
            detail_start = _find_field(fields, ("sessionstart", "starttime"))
            detail_end = _find_field(fields, ("sessionend", "endtime"))
            if detail_date and _parse_date(detail_date):
                booking["session_date"] = _parse_date(detail_date)
            if detail_start and _parse_time(detail_start):
                booking["start_time"] = _parse_time(detail_start)
            if detail_end and _parse_time(detail_end):
                booking["end_time"] = _parse_time(detail_end)
            detail_session_id = _find_field(fields, ("officialsessionid", "sessionid"))
            if detail_session_id:
                booking["official_session_id"] = detail_session_id
            owner = _find_field(fields, ("owneruserid", "bookingownerid", "customeruserid"))
            booking["owner_user_id"] = owner or None
            before_pages = list(page.context.pages)
            try:
                button = page.get_by_text(re.compile(r"view\s+attendees", re.I)).first
                if button.count():
                    button.click(timeout=5000)
                    time.sleep(.5)
            except Exception:
                pass
            attendee_page = page
            for candidate_page in page.context.pages:
                if candidate_page not in before_pages or "attendee" in candidate_page.url.casefold():
                    attendee_page = candidate_page
                    break
            attendee_ids = _attendee_ids(attendee_page)
            if attendee_page is not page:
                try: attendee_page.close()
                except Exception: pass
            if not attendee_ids:
                return {'ok': False, 'login_required': False, 'match': 'attendees_unavailable', 'bookings': [], 'users': [],
                        'message': 'Could not verify the attendee list for every booking. Open View attendees and retry; sending is blocked.'}
            booking["attendee_user_ids"] = attendee_ids
            imported_bookings.append(booking)
            for uid in list(dict.fromkeys(attendee_ids + ([owner] if owner else []))):
                if uid and uid not in users_by_id:
                    users_by_id[uid] = _lookup_user(page, uid)
        if len(imported_bookings) != len(selected_rows):
            return {"ok": False, "login_required": False, "match": "matched_but_details_unavailable",
                    "bookings": [], "users": [],
                    "message": "ظهر الحجز لكن ما قدرت أقرأ تفاصيله أو قائمة View attendees. لم تُسحب قائمة مستخدمين عامة."}
        final_match = match_bookings(recording, imported_bookings)
        if final_match['status'] != 'matched' or len(final_match['bookings']) != len(imported_bookings):
            return {'ok': False, 'match': 'details_changed', 'bookings': [], 'users': [],
                    'message': 'EVA booking details changed or refer to different games. Review the actual play slot before sending.'}
        return {"ok": True, "login_required": False, "match": "matched", "bookings": imported_bookings,
                "users": list(users_by_id.values()), "scoped_user_lookup": True,
                "message": f"تم جلب {len(imported_bookings)} حجوزات مطابقة وحساباتها المرتبطة فقط."}


from copy import deepcopy

import threading


class BookingSync:
    def __init__(self, executor, fetch, clock=time.time):
        self.executor, self.fetch, self.clock = executor, fetch, clock
        self.lock = threading.RLock()
        self.future = None
        self.next_at = 0
        self.failures = 0
        self.paused_login = False
        self.state = {'ok': False, 'status': 'connecting', 'health': 'connecting', 'sessions': [], 'bookings': [],
                      'updated_at': None, 'error': None, 'target_interval_seconds': 1, 'stale': True}

    def resume(self):
        with self.lock:
            self.paused_login = False
            self.next_at = 0

    def snapshot(self):
        with self.lock:
            state = deepcopy(self.state)
            state['busy'] = self.future is not None
            state['stale'] = not state['ok'] or state['updated_at'] is None or self.clock()-state['updated_at'] > 15
            return state

    def tick(self):
        with self.lock:
            now = self.clock()
            if self.future is not None:
                if not self.future.done():
                    return
                try:
                    result = self.future.result()
                    if not result.get('ok'):
                        self.paused_login = bool(result.get('login_required'))
                        raise RuntimeError(result.get('message') or 'EVA Admin disconnected')
                    self.failures = 0
                    self.state.update(ok=True, status='connected', health='connected', sessions=result.get('sessions', []),
                        bookings=result.get('bookings', []), updated_at=now, error=None, session_date=self.request_day)
                except Exception as exc:
                    self.failures += 1
                    health = 'needs_signin' if self.paused_login else 'changed' if isinstance(exc, LayoutChanged) else 'reconnecting'
                    self.state.update(ok=False, status='login_required' if self.paused_login else 'reconnecting', health=health, error=str(exc)[:300])
                    self.next_at = now + min(60, 2 ** min(self.failures, 6))
                finally:
                    self.future = None
            if not self.paused_login and now >= self.next_at:
                self.request_day = datetime.now(BAHRAIN).date().isoformat()
                self.future = self.executor.submit(self.fetch, self.request_day)
                self.next_at = now + 1
