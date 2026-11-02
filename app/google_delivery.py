"""Google OAuth, private Drive sharing, and Gmail delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from app_paths import DATA, settings

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/drive.file"]
KEYRING_SERVICE = "EVA Recording Manager Google OAuth"
KEYRING_ACCOUNT = "google-user-token"
STREAMLABS_KEYRING_SERVICE = "EVA Recording Manager Streamlabs"
STREAMLABS_KEYRING_ACCOUNT = "remote-control-token"
OBS_KEYRING_SERVICE = "EVA Recording Manager OBS"
OBS_KEYRING_ACCOUNT = "websocket-password"
CLIENT_FILE = DATA / 'config' / 'google_oauth_client.json'


def find_client_file():
    """The Google 'Desktop app' client file. Accepts the exact name, or the file as Google downloads it
    (client_secret_....json), or a name Windows changed (e.g. google_oauth_client.json.json / (1))."""
    if CLIENT_FILE.is_file():
        return CLIENT_FILE
    folder = CLIENT_FILE.parent
    try:
        candidates = sorted((f for f in folder.glob('*.json*') if f.is_file() and f.stat().st_size < 64_000),
                            key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get('installed'), dict) and data['installed'].get('client_id'):
            return f
    return None
_AUTH_LOCK = threading.Lock()
_SIGNIN_STATE = {"status": "signed_out", "error": None}


class GoogleIntegrationError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
        from keyring.backends import Windows
    except ImportError as exc:
        raise GoogleIntegrationError("Required libraries are missing. Run START_EVA_MANAGER.bat again.") from exc
    backend = keyring.get_keyring()
    if not isinstance(backend, Windows.WinVaultKeyring):
        raise GoogleIntegrationError("Google sign-in requires Windows Credential Manager for secure token storage.")
    return keyring


def get_streamlabs_token() -> str | None:
    return _keyring().get_password(STREAMLABS_KEYRING_SERVICE, STREAMLABS_KEYRING_ACCOUNT)


def save_streamlabs_token(token: str) -> None:
    _keyring().set_password(STREAMLABS_KEYRING_SERVICE, STREAMLABS_KEYRING_ACCOUNT, token)


def get_obs_password() -> str | None:
    return _keyring().get_password(OBS_KEYRING_SERVICE, OBS_KEYRING_ACCOUNT)


def save_obs_password(password: str) -> None:
    _keyring().set_password(OBS_KEYRING_SERVICE, OBS_KEYRING_ACCOUNT, password)


def sign_in_state():
    try:
        keyring = _keyring()
        saved = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return {"signed_in": bool(saved), "status": _SIGNIN_STATE["status"],
                "error": _SIGNIN_STATE["error"], "client_configured": find_client_file() is not None}
    except GoogleIntegrationError as exc:
        return {"signed_in": False, "status": _SIGNIN_STATE["status"],
                "error": str(exc), "client_configured": find_client_file() is not None}


def open_setup_folder() -> None:
    CLIENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        raise GoogleIntegrationError("Open the config folder on the Streaming PC and add the Google OAuth file.")
    os.startfile(str(CLIENT_FILE.parent))


def begin_sign_in() -> None:
    if _SIGNIN_STATE["status"] == "signing_in":
        return
    client = find_client_file()
    if client is None:
        raise GoogleIntegrationError("The Google OAuth file was not found in " + str(CLIENT_FILE.parent) + ". Use the 'Desktop app' client type, download its JSON and put it in that folder. If Google gave a 'Web application' file, create a new Desktop one.")
    _SIGNIN_STATE.update(status="signing_in", error=None)

    def worker():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
            credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                                access_type="offline", prompt="consent", timeout_seconds=180)
            _keyring().set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, credentials.to_json())
            _SIGNIN_STATE.update(status="signed_in", error=None)
        except Exception as exc:
            _SIGNIN_STATE.update(status="error", error=f"Google sign-in failed: {type(exc).__name__}: {exc}")

    threading.Thread(target=worker, name="google-oauth", daemon=True).start()


def _credentials():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        keyring = _keyring()
        saved = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if not saved:
            raise GoogleIntegrationError("Sign in to Gmail first")
        creds = Credentials.from_authorized_user_info(json.loads(saved), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, creds.to_json())
        if not creds.valid:
            raise GoogleIntegrationError("Google sign-in expired. Sign in again from the app.")
        return creds
    except GoogleIntegrationError:
        raise
    except Exception as exc:
        raise GoogleIntegrationError(f"Could not refresh Google sign-in: {type(exc).__name__}: {exc}") from exc


def forget_sign_in() -> None:
    keyring = _keyring()
    if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT):
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    _SIGNIN_STATE.update(status='signed_out', error=None)


def video_fingerprint(video_path: str) -> tuple[Path, str]:
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise GoogleIntegrationError("Choose a video file that exists on the Streaming PC")
    if path.stat().st_size == 0:
        raise GoogleIntegrationError("Video file is empty")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return path, digest.hexdigest()


def _services():
    try:
        from googleapiclient.discovery import build
        creds = _credentials()
        import google_auth_httplib2, httplib2
        def transport():
            return google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=45))
        return (build("drive", "v3", http=transport(), cache_discovery=False),
                build("gmail", "v1", http=transport(), cache_discovery=False))
    except GoogleIntegrationError:
        raise
    except Exception as exc:
        raise GoogleIntegrationError(f"Google API is unavailable: {type(exc).__name__}: {exc}") from exc


def _drive_video(drive, path: Path, sha256: str, store):
    asset = store.get_video_asset(sha256)
    file_id = asset.get("drive_file_id") if asset else None
    if not file_id:
        try:
            # Recover a completed upload whose final response was lost after an internet failure.
            found = drive.files().list(q="trashed = false and appProperties has { key='evaSha256' and value='" + sha256 + "' }",
                                      spaces='drive', fields='files(id,webViewLink)', pageSize=10).execute().get('files', [])
            if found:
                uploaded = found[0]
                file_id = uploaded['id']
                store.save_video_asset(sha256, str(path), file_id, uploaded.get('webViewLink', ''))
                return file_id
            from googleapiclient.http import MediaFileUpload
            mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
            uploaded = drive.files().create(body={"name": path.name, "mimeType": mime, 'appProperties': {'evaSha256': sha256}},
                                             media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=8*1024**2),
                                             fields="id,name,webViewLink").execute()
            file_id = uploaded["id"]
            store.save_video_asset(sha256, str(path), file_id, uploaded.get("webViewLink", ""))
        except Exception as exc:
            raise GoogleIntegrationError(f"Video upload failed: {type(exc).__name__}: {exc}") from exc
    return file_id


def _http_status(exc):
    return getattr(getattr(exc, 'resp', None), 'status', None) or getattr(exc, 'status_code', None)


def _ensure_private_share(drive, file_id: str, email: str):
    """Give exactly this email read access and confirm Drive lists it before any email is sent."""
    def listed():
        permissions = drive.permissions().list(fileId=file_id, fields="permissions(id,emailAddress,type,role)").execute()
        return {str(p.get("emailAddress", "")).casefold() for p in permissions.get("permissions", [])
                if p.get("type") == "user"}
    try:
        if email.casefold() not in listed():
            body = {"type": "user", "role": "reader", "emailAddress": email}
            try:
                drive.permissions().create(fileId=file_id, body=body, sendNotificationEmail=False,
                                           fields="id,emailAddress,role").execute()
            except Exception as exc:
                # Addresses without a Google account can only be invited through Drive's own notification.
                if _http_status(exc) != 400:
                    raise
                drive.permissions().create(fileId=file_id, body=body, sendNotificationEmail=True,
                                           fields="id,emailAddress,role").execute()
            if email.casefold() not in listed():
                raise GoogleIntegrationError("Drive did not confirm the sharing permission")
        return f"https://drive.google.com/file/d/{file_id}/view"
    except GoogleIntegrationError as exc:
        raise GoogleIntegrationError(f"Private Drive sharing failed for {email}: {exc}") from exc
    except Exception as exc:
        raise GoogleIntegrationError(f"Private Drive sharing failed for {email}: {type(exc).__name__}: {exc}") from exc


def _sender_email(drive):
    """Drive about supports the existing drive.file scope; Gmail getProfile does not support gmail.send."""
    try:
        value = drive.about().get(fields='user(emailAddress)').execute()['user']['emailAddress']
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('The signed-in account did not return a valid email address')
        return value
    except Exception as exc:
        raise GoogleIntegrationError('Could not read the signed-in sender account: ' + str(exc)) from exc

def _send_message(gmail, to_email: str, display_name: str, video_url: str,
                  session_label: str, booking_ids: list[str], sender_email: str):
    try:
        message = EmailMessage()
        message["To"] = to_email
        message['From'] = sender_email
        message['Date'] = formatdate(localtime=False, usegmt=True)
        message["Subject"] = "Your EVA session recording"
        message.set_content(
            f"Hi {display_name or 'there'},\n\n"
            f"Your EVA session recording is ready.\n"
            f"Session: {session_label}\n"
            f"Booking: {', '.join(booking_ids)}\n\n"
            f"Watch or download your video: {video_url}\n\n"
            f"EVA {(settings().get('venue_label') or 'Bahrain').strip()[:60]}"
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:
        raise GoogleIntegrationError(f"Gmail send result is uncertain. Check Sent mail before retrying. {type(exc).__name__}: {exc}") from exc


def deliver_video(store, video_path: str, recording: dict, recipients: list[dict], booking_ids: list[str]):
    if not recipients:
        raise GoogleIntegrationError("No verified booking-linked email recipients were selected")
    if any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(r.get("email", ""))) for r in recipients):
        raise GoogleIntegrationError("A selected recipient email is missing or malformed")
    path, digest = video_fingerprint(video_path)
    drive, gmail = _services()
    sender = _sender_email(drive)
    file_id = _drive_video(drive, path, digest, store)
    session_label = f"{recording['session_date']} {recording['start_time']}–{recording['end_time']} Bahrain time"
    outcomes = []
    for recipient in recipients:
        email = recipient["email"].strip()
        if not store.reserve_delivery(digest, email, "sending", datetime.now(timezone.utc).isoformat(timespec="seconds")):
            outcomes.append({"email": email, "status": "already_processed"})
            continue
        try:
            link = _ensure_private_share(drive, file_id, email)
            # Once this call begins, a network error could mean Gmail accepted the message.
            store.update_delivery(digest, email, "uncertain")
            sent = _send_message(gmail, email, recipient.get("name", ""), link, session_label, booking_ids, sender)
            store.update_delivery(digest, email, "sent", sent.get("id"))
            outcomes.append({"email": email, "status": "sent"})
        except GoogleIntegrationError as exc:
            outcome_status = "failed" if "Gmail send result is uncertain" not in str(exc) else "uncertain"
            store.update_delivery(digest, email, outcome_status)
            outcomes.append({"email": email, "status": outcome_status, "error": str(exc)})
    return {"video_sha256": digest, "drive_file_id": file_id, "outcomes": outcomes}

def deliver_session(store, recording, recipients, booking_ids, progress=lambda state: None):
    """Upload both assets, grant access to both, send one message with both links."""
    if recording.get('status') != 'ready_for_review' or not recording.get('camera_path'):
        raise GoogleIntegrationError('Only a complete dual recording can be delivered')
    if not recipients or any(not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', str(r.get('email', ''))) for r in recipients):
        raise GoogleIntegrationError('Select valid booking-linked recipient emails')
    game, game_hash = video_fingerprint(recording['output_path'])
    camera, camera_hash = video_fingerprint(recording['camera_path'])
    pair_hash = hashlib.sha256((game_hash + ':' + camera_hash).encode()).hexdigest()
    drive, gmail = _services()
    sender = _sender_email(drive)
    progress('uploading_game')
    game_id = _drive_video(drive, game, game_hash, store)
    progress('uploading_camera')
    camera_id = _drive_video(drive, camera, camera_hash, store)
    label = f"{recording['session_date']} {recording['start_time']}–{recording['end_time']} Bahrain time"
    outcomes = []
    for recipient in recipients:
        email = recipient['email'].strip()
        ledger = store.get_delivery(pair_hash, email)
        if ledger and ledger['status'] in {'sent', 'uncertain', 'sending'}:
            outcomes.append({'email': email, 'status': 'sent' if ledger['status'] == 'sent' else 'uncertain'})
            continue
        if not store.reserve_delivery(pair_hash, email, 'preparing', datetime.now(timezone.utc).isoformat()):
            # A crash before the Gmail boundary is safe to retry.
            if not ledger or ledger['status'] != 'preparing':
                outcomes.append({'email': email, 'status': 'uncertain'})
                continue
        try:
            progress('sharing')
            game_url = _ensure_private_share(drive, game_id, email)
            camera_url = _ensure_private_share(drive, camera_id, email)
            progress('emailing')
            store.update_delivery(pair_hash, email, 'uncertain')
            sent = _send_message(gmail, email, recipient.get('name', ''),
                                 f'Game: {game_url}\nCamera: {camera_url}', label, booking_ids, sender)
            store.update_delivery(pair_hash, email, 'sent', sent.get('id'))
            outcomes.append({'email': email, 'status': 'sent'})
        except GoogleIntegrationError as exc:
            uncertain = (store.get_delivery(pair_hash, email) or {}).get('status') == 'uncertain'
            state = 'uncertain' if uncertain else 'failed'
            store.update_delivery(pair_hash, email, state)
            outcomes.append({'email': email, 'status': state, 'error': str(exc)})
    return {'outcomes': outcomes, 'video_sha256': pair_hash, 'drive_file_ids': [game_id, camera_id]}


def send_test_email(store=None):
    """Prove Google works end to end without involving a customer: upload a tiny file to Drive and
    email its link to the signed-in Google account itself. Earlier test files are removed first."""
    drive, gmail = _services()
    sender = _sender_email(drive)
    try:
        old = drive.files().list(q="trashed = false and appProperties has { key='evaTest' and value='1' }",
                                 spaces='drive', fields='files(id)', pageSize=20).execute().get('files', [])
        for item in old:
            try:
                drive.files().delete(fileId=item['id']).execute()
            except Exception:
                pass
        import io
        from googleapiclient.http import MediaIoBaseUpload
        data = b'EVA connection test. You can delete this file.\n' * 40
        uploaded = drive.files().create(
            body={'name': 'EVA-connection-test.mp4', 'mimeType': 'video/mp4', 'appProperties': {'evaTest': '1'}},
            media_body=MediaIoBaseUpload(io.BytesIO(data), mimetype='video/mp4', resumable=False),
            fields='id,webViewLink').execute()
        link = f"https://drive.google.com/file/d/{uploaded['id']}/view"
    except Exception as exc:
        raise GoogleIntegrationError(f'Drive upload test failed: {type(exc).__name__}: {exc}') from exc
    try:
        message = EmailMessage()
        message['To'], message['From'] = sender, sender
        message['Date'] = formatdate(localtime=False, usegmt=True)
        message['Subject'] = 'EVA connection test'
        message.set_content('Google Drive and Gmail are working for EVA.\n\n'
                            f'Test file (only you can open it): {link}\n\n'
                            'No customer was contacted. You can delete this email and the test file.')
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
        sent = gmail.users().messages().send(userId='me', body={'raw': raw}).execute()
    except Exception as exc:
        raise GoogleIntegrationError(f'Gmail test failed: {type(exc).__name__}: {exc}') from exc
    return {'ok': True, 'sender': sender, 'message_id': sent.get('id'), 'drive_file_id': uploaded['id']}
