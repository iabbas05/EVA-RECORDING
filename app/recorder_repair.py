"""Reconnect safely; never launch a replacement recorder mid-session."""
import os
import subprocess
import threading
import time
from pathlib import Path

_last_attempt = 0
_last_launch = {}
RELAUNCH_SECONDS = 600     # an app EVA already started is not started again for 10 minutes
_lock = threading.Lock()

def repair(service, factory, force=False):
    global _last_attempt
    with _lock, service.lock:
        if service.active_id:
            return {'launched': [], 'message': 'Active session: reconnecting existing connections only'}
        now = time.monotonic()
        if not force and now - _last_attempt < 60:
            return {'launched': [], 'message': 'Waiting before the next repair attempt'}
        _last_attempt = now
        state = service.obs.status()
        if state.get('recording'):
            return {'launched': [], 'message': 'Recording outside EVA. Stop it before repair.'}
        launched = []
        if os.name == 'nt':
            # Query process names without exposing command lines or credentials.
            result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=8,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            processes = result.stdout.casefold()
            paths = {
                'OBS': ('obs64.exe', [Path(os.getenv('ProgramFiles', '')) / 'obs-studio/bin/64bit/obs64.exe']),
                'Streamlabs': ('streamlabs', [Path(os.getenv('ProgramFiles', '')) / 'Streamlabs Desktop/Streamlabs Desktop.exe',
                    Path(os.getenv('LOCALAPPDATA', '')) / 'Programs/streamlabs-desktop/Streamlabs Desktop.exe',
                    Path(os.getenv('LOCALAPPDATA', '')) / 'slobs-client/Streamlabs OBS.exe'])}
            for name, (process, candidates) in paths.items():
                if name == 'Streamlabs' and service.config().get('camera_backend') == 'obs' and service.config().get('recording_layout', 'obs_game') == 'obs_game':
                    continue
                if process in processes:
                    continue
                if not force and now - _last_launch.get(name, -RELAUNCH_SECONDS) < RELAUNCH_SECONDS:
                    continue
                exe = next((p for p in candidates if p.is_file()), None)
                if exe:
                    subprocess.Popen([str(exe)], cwd=exe.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     startupinfo=_hidden_startup())
                    launched.append(name)
                    _last_launch[name] = now
        service.obs = factory()
        return {'launched': launched, 'message': 'Connection repair requested'}

def _hidden_startup():
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return startup
