"""Install-time and runtime checks for EVA Recording Manager."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from app_paths import RESOURCE, settings


ROOT = RESOURCE
REQUIRED_MODULES = {
    "obsws_python": "OBS WebSocket",
    "websocket": "Streamlabs WebSocket",
    "keyring": "Windows Credential Manager",
    "googleapiclient": "Google APIs",
    "google_auth_oauthlib": "Google OAuth",
    "playwright.sync_api": "EVA browser",
    "webview": "Windows desktop shell",
    'av': 'Video stream verification',
}
REQUIRED_FILES = (
    "manager.py", "index.html", "eva.ico", "matching.py", "storage.py",
    "eva_portal.py", "google_delivery.py", "streamlabs_control.py", "dual_recorder.py", "desktop.py",
)


def _check(check_id: str, label: str, ok: bool, message: str, required: bool = True) -> dict:
    return {"id": check_id, "label": label, "ok": bool(ok), "message": message, "required": required}


def install_checks() -> list[dict]:
    checks = []
    missing_files = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    checks.append(_check("app_files", "Application files", not missing_files,
                         "All application files are present" if not missing_files else "Missing: " + ", ".join(missing_files)))

    for module, label in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module)
            checks.append(_check("module_" + module.replace(".", "_"), label, True, "Installed"))
        except Exception as exc:
            checks.append(_check("module_" + module.replace(".", "_"), label, False,
                                 f"Import failed: {type(exc).__name__}: {exc}"))

    if os.name == "nt":
        chrome_candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        try:
            from eva_portal import find_browser
            name, exe, _profile = find_browser()
        except Exception:
            name, exe = "Chrome", None
        browser = Path(exe) if exe else next((p for p in chrome_candidates if p.is_file()), None)
        checks.append(_check("chrome", f"EVA Admin browser ({name})", browser is not None and browser.is_file(),
                             str(browser) if browser else "Install Brave or Google Chrome"))
    else:
        checks.append(_check("chrome", "EVA Admin browser", True, "Checked on Windows during installation", required=False))

    try:
        with tempfile.TemporaryDirectory(prefix="eva-self-test-") as directory:
            path = Path(directory) / "health.sqlite3"
            # A sqlite connection context commits or rolls back but does not close
            # the handle. Close it explicitly so Windows can remove the temp file.
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE health(value TEXT NOT NULL)")
                db.execute("INSERT INTO health VALUES ('ok')")
                value = db.execute("SELECT value FROM health").fetchone()[0]
                db.commit()
            checks.append(_check("sqlite", "Local database", value == "ok", "Read/write test passed"))
    except Exception as exc:
        checks.append(_check("sqlite", "Local database", False, f"Read/write failed: {type(exc).__name__}: {exc}"))

    if os.name == "nt":
        try:
            import keyring
            backend = keyring.get_keyring()
            backend_name = f"{type(backend).__module__}.{type(backend).__name__}"
            ok = "Windows" in backend_name or "WinVault" in backend_name
            checks.append(_check("credential_manager", "Windows Credential Manager", ok, backend_name))
        except Exception as exc:
            checks.append(_check("credential_manager", "Windows Credential Manager", False,
                                 f"Check failed: {type(exc).__name__}: {exc}"))
    return checks


def runtime_checks(store, recorder, mode: str, google_state: dict, eva_profile_dir: Path) -> dict:
    checks = install_checks()
    try:
        db_result = store.healthcheck()
        checks.append(_check("app_database", "Application database", db_result == "ok", db_result))
    except Exception as exc:
        checks.append(_check("app_database", "Application database", False,
                             f"Check failed: {type(exc).__name__}: {exc}"))

    try:
        recorder_status = recorder.status()
        connected = bool(recorder_status.get("connected"))
        checks.append(_check("recorder", f"Recorder ({mode})", connected,
                             "Connected" if connected else recorder_status.get("error", "Not connected"),
                             required=mode not in {"manual", "simulation"}))
    except Exception as exc:
        recorder_status = {"connected": False, "recording": False, "mode": mode, "error": str(exc)}
        checks.append(_check("recorder", f"Recorder ({mode})", False,
                             f"Check failed: {type(exc).__name__}: {exc}"))

    profile_saved = eva_profile_dir.is_dir() and any(eva_profile_dir.iterdir())
    checks.append(_check("eva_profile", "EVA saved browser", profile_saved,
                         "Saved browser profile found" if profile_saved else "Open EVA and sign in once",
                         required=False))
    try:
        from eva_portal import admin_session_saved
        saved = admin_session_saved()
    except Exception:
        saved = False
    checks.append(_check("eva_signin_saved", "EVA Admin saved sign-in", saved,
                         "Saved securely on this PC" if saved else "Not saved yet: Sign in to EVA Admin, then Check EVA connection",
                         required=False))
    checks.append(_check("google_client", "Google OAuth file", bool(google_state.get("client_configured")),
                         "Configured" if google_state.get("client_configured") else "OAuth Desktop file is not configured",
                         required=False))
    checks.append(_check("google_signin", "Gmail / Google Drive", bool(google_state.get("signed_in")),
                         "Signed in" if google_state.get("signed_in") else google_state.get("error") or "Not signed in",
                         required=False))
    import shutil
    config = settings()
    for key in ('game_folder', 'camera_folder'):
        folder = Path(config.get(key) or '__not_configured__')
        ok = folder.is_dir() and shutil.disk_usage(folder).free >= 2 * 1024**3
        checks.append(_check(key, key.replace('_', ' ').title(), ok, str(folder) if ok else 'Set an existing recording folder with at least 2 GB free'))
    checks.append(_check('venue', 'EVA venue', bool(config.get('venue_label')),
                         config.get('venue_label') or 'Detected the first time EVA Admin is checked', required=False))
    checks.append(_check('separate_folders', 'Separate recording folders', bool(config.get('game_folder') and config.get('camera_folder') and Path(config['game_folder']).resolve() != Path(config['camera_folder']).resolve()), 'Game and Camera must save separately'))
    required_ok = all(item["ok"] for item in checks if item["required"])
    return {"ok": required_ok, "version": (ROOT / "version.txt").read_text(encoding="utf-8").strip(),
            "mode": mode, "recorder": recorder_status, "checks": checks}


def main() -> int:
    result = {"checks": install_checks()}
    result["ok"] = all(item["ok"] for item in result["checks"] if item["required"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
