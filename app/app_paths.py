"""Separate writable installation/data paths from PyInstaller's resource directory."""
import json
import os
import sys
import threading
from pathlib import Path

RESOURCE = Path(__file__).resolve().parent
INSTALL = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RESOURCE
DATA = Path(os.getenv('EVA_DATA_DIR')) if os.getenv('EVA_DATA_DIR') else Path(os.getenv('LOCALAPPDATA', str(Path.home() / '.local'))) / 'EVARecordingManager'
VERSION = (RESOURCE / 'version.txt').read_text(encoding='utf-8-sig').strip()
PAIRING_ID = 'EVA-JH'
_lock = threading.RLock()

def settings():
    with _lock:
        path = DATA / 'settings.json'
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}

def save_settings(**values):
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        value = settings()
        value.update(values)
        temp = DATA / 'settings.json.tmp'
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, DATA / 'settings.json')
        return value
