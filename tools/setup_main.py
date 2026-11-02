"""EVA one-file installer.

Double-click EVA_Setup.exe. It contains the complete EVA update, checks it is intact, offers to download
anything EVA needs that is missing (Brave or Chrome, Edge WebView2), then runs the same transactional
installer as START_HERE.bat: it verifies every file, keeps your settings and recordings, and restores the
previous version automatically if the new one does not start.

Only the Python standard library is used. Nothing is sent anywhere by this program.
"""
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

VERSION = '__VERSION__'
PACKAGE_NAME = 'EVA_v__VERSION___UPDATE.zip'
PACKAGE_SHA256 = '__SHA256__'
LAYOUT = 'streamlabs_game'                     # Game = Streamlabs, Camera = OBS (as START_HERE.bat)
TARGET = Path(os.getenv('LOCALAPPDATA') or str(Path.home())) / 'EVARecordingManager'
LOG = Path(tempfile.gettempdir()) / 'EVA-Setup.log'
WEBVIEW2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'

MB_OK, MB_YESNO, MB_ERROR, MB_QUESTION, MB_INFO, MB_TOPMOST = 0x0, 0x4, 0x10, 0x20, 0x40, 0x40000
IDYES = 6


def log(message):
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(message) + '\n')
    except OSError:
        pass


def box(text, flags=MB_OK | MB_INFO, title='EVA Setup'):
    """Windows message box. Returns IDYES (6) for Yes. Prints instead when there is no Windows."""
    log('BOX ' + text.replace('\n', ' | '))
    if os.name != 'nt' or os.getenv('EVA_SETUP_NO_UI') == '1':
        print(text)
        return IDYES
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST)


def resource_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_payload(path):
    if not path.is_file():
        raise RuntimeError('The EVA update is missing from this installer. Download EVA Setup again.')
    if sha256_of(path) != PACKAGE_SHA256:
        raise RuntimeError('This installer is damaged (its update does not match its checksum). Download EVA Setup again.')


def extract(package, destination):
    """Unpack the update into a fresh folder. Only flat file names are accepted."""
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            name = info.filename
            if '/' in name or '\\' in name or name.startswith('.') or not name:
                raise RuntimeError('The update contains an unexpected path: ' + name)
        archive.extractall(destination)
    if not (Path(destination) / 'install_update.ps1').is_file():
        raise RuntimeError('The update does not contain its installer.')
    release = json.loads((Path(destination) / 'release.json').read_text(encoding='utf-8-sig'))
    if release.get('version') != VERSION:
        raise RuntimeError('The update is version %s, expected %s.' % (release.get('version'), VERSION))
    return release


# ------------------------------------------------------------------ prerequisites
def webview2_installed():
    """Microsoft Edge WebView2 Runtime (EVA's window). Present on current Windows 10/11 in most cases."""
    try:
        import winreg
    except ImportError:
        return True
    keys = [(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\' + WEBVIEW2_GUID),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\EdgeUpdate\Clients\\' + WEBVIEW2_GUID),
            (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\EdgeUpdate\Clients\\' + WEBVIEW2_GUID)]
    for root, key in keys:
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, 'pv')
                if value and value != '0.0.0.0':
                    return True
        except OSError:
            continue
    return False


def browser_installed():
    roots = [os.getenv('ProgramFiles', ''), os.getenv('ProgramFiles(x86)', ''), os.getenv('LOCALAPPDATA', '')]
    for root in filter(None, roots):
        for relative in (r'BraveSoftware\Brave-Browser\Application\brave.exe', r'Google\Chrome\Application\chrome.exe'):
            if (Path(root) / relative).is_file():
                return True
    return False


def winget_install(package_id, label):
    """Download and install with Windows' own package manager, in a visible window. Returns True on success."""
    winget = shutil.which('winget')
    if not winget:
        return False
    log('winget install ' + package_id)
    command = [winget, 'install', '--id', package_id, '--exact', '--silent',
               '--accept-package-agreements', '--accept-source-agreements']
    try:
        flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
        return subprocess.run(command, creationflags=flags, env=clean_env(), timeout=1800).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_prerequisites(ask=box, installer=winget_install):
    notes = []
    if not browser_installed():
        if ask('EVA needs Brave (or Google Chrome) to read your EVA Admin bookings, and neither was found.\n\n'
               'Download and install Brave now?', MB_YESNO | MB_QUESTION) == IDYES:
            if not (installer('Brave.Brave', 'Brave') and browser_installed()):
                notes.append('Brave could not be installed automatically. Install it from brave.com/download, then open EVA.')
        else:
            notes.append('Install Brave or Google Chrome before using EVA Admin sync.')
    if not webview2_installed():
        if ask('EVA needs the Microsoft Edge WebView2 Runtime to show its window, and it was not found.\n\n'
               'Download and install it now?', MB_YESNO | MB_QUESTION) == IDYES:
            if not installer('Microsoft.EdgeWebView2Runtime', 'WebView2'):
                notes.append('WebView2 could not be installed automatically. Install "Microsoft Edge WebView2 Runtime" from microsoft.com.')
        else:
            notes.append('Install the Microsoft Edge WebView2 Runtime before opening EVA.')
    return notes


# ------------------------------------------------------------------ run the transactional installer
def clean_env():
    """A PyInstaller program must not pass its own bootloader variables to another PyInstaller program (EVA.exe)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('_PYI', '_MEIPASS'))}
    mei = str(getattr(sys, '_MEIPASS', '')).casefold()
    if mei and 'PATH' in env:
        env['PATH'] = os.pathsep.join(p for p in env['PATH'].split(os.pathsep) if p.casefold().rstrip('\\/') != mei.rstrip('\\/'))
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def read_result():
    path = TARGET / 'updates' / 'last-result.json'
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def run_installer(folder, package, runner=subprocess.run):
    """Same call START_HERE.bat makes, in a visible console that stays open if something fails."""
    script = Path(folder) / 'run_install.cmd'
    script.write_text('\r\n'.join([
        '@echo off', 'title EVA setup',
        'echo Installing EVA %s. Please wait; do not close this window.' % VERSION, 'echo.',
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%EVA_PS1%" -PackagePath "%EVA_ZIP%" -RecordingLayout ' + LAYOUT,
        'set CODE=%ERRORLEVEL%',
        'if not "%CODE%"=="0" (', '  echo.', '  echo EVA setup did not finish. Read the message above, then close this window.', '  pause', ')',
        'exit /b %CODE%', '']), encoding='ascii')
    env = clean_env()
    env['EVA_PS1'] = str(Path(folder) / 'install_update.ps1')
    env['EVA_ZIP'] = str(package)
    result = runner(['cmd.exe', '/c', str(script)], env=env, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
    return result.returncode


def main(argv):
    log('EVA Setup %s started: %s' % (VERSION, argv))
    package = resource_dir() / PACKAGE_NAME
    if '--verify' in argv:                      # checks the installer itself; installs nothing
        check_payload(package)
        with tempfile.TemporaryDirectory(prefix='eva-setup-check-') as folder:
            release = extract(package, folder)
        print(json.dumps({'ok': True, 'version': release['version'], 'files': len(release['files'])}))
        return 0
    if box('Install EVA %s on this PC?\n\n'
           'Before you continue:\n'
           '  • stop any recording in EVA, OBS and Streamlabs\n'
           '  • close EVA if it is open\n\n'
           'Your settings, sign-ins and recordings are kept. If anything goes wrong, the previous version is restored '
           'automatically.' % VERSION, MB_YESNO | MB_QUESTION) != IDYES:
        return 2
    folder = Path(tempfile.mkdtemp(prefix='eva-setup-'))
    try:
        check_payload(package)
        notes = ensure_prerequisites()
        extract(package, folder)
        try:
            (TARGET / 'updates' / 'last-result.json').unlink()   # never trust a result left by an earlier install
        except OSError:
            pass
        code = run_installer(folder, package)
        result = read_result()
        if code == 0 and result.get('ok') and result.get('version') == VERSION:
            text = 'EVA %s is installed.\n\nOpen EVA from the desktop shortcut. First time on this PC: Settings → Sign in to EVA Admin, ' \
                   'then Test Drive & Gmail.' % VERSION
            if notes:
                text += '\n\nStill to do:\n  • ' + '\n  • '.join(notes)
            box(text, MB_OK | MB_INFO)
            return 0
        reason = result.get('error') or 'The installer stopped before finishing.'
        box('EVA was not installed.\n\n%s\n\nYour previous EVA was kept or restored. Details: %s' % (reason, LOG), MB_OK | MB_ERROR)
        return 1
    except Exception as exc:
        log(traceback.format_exc())
        box('EVA setup could not continue.\n\n%s\n\nDetails: %s' % (exc, LOG), MB_OK | MB_ERROR)
        return 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
