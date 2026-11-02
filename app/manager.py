"""LAN control API and background recovery for the EVA desktop application."""
import ipaddress
import json
import logging
import re
import os
import secrets
import socket
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app_paths import DATA, RESOURCE, VERSION, PAIRING_ID, settings, save_settings
from storage import Store
from service import RecordingService
from matching import match_bookings, recipients_for_bookings
from obs_control import ObsUnavailable, ObsWebSocket, UnavailableRecorder
from streamlabs_control import build_streamlabs, projector_windows
from dual_recorder import DualRecorder
from google_delivery import (GoogleIntegrationError, begin_sign_in, forget_sign_in, get_obs_password,
    get_streamlabs_token, save_obs_password, save_streamlabs_token, open_setup_folder, sign_in_state, _keyring,
    send_test_email)
from eva_portal import (PROFILE_DIR, open_eva, pull_recording_session, verify_eva, day_bookings, session_recipients,
    EvaPortalError, sign_out_admin, admin_session_saved, close_browser, find_browser, signin_progress)
from diagnostics import runtime_checks
from delivery_queue import DeliveryQueue, cleanup_old_videos
from eva_portal import BookingSync
from recorder_repair import repair
from updater import DEFAULT_MANIFEST, UpdateError, check, stage, launch_update, resolve_package, validate_package, MAX_ARCHIVE

LOG = logging.getLogger('eva')
DATA.mkdir(parents=True, exist_ok=True)
STORE = Store(str(DATA / 'smoke.sqlite3') if os.getenv('EVA_UPDATE_VALIDATING') == '1' else os.getenv('EVA_MANAGER_DB', str(DATA / 'eva_recording_manager.sqlite3')))
MODE = 'dual'
ACCESS_TOKEN = PAIRING_ID
MAINTENANCE = threading.RLock()
UPDATING = False
EVA_STATE = {'status': 'not_synced', 'message': 'Open EVA Admin once to sign in, then Sync & review a session.'}
EVA_BROWSER_QUEUE = ThreadPoolExecutor(max_workers=1, thread_name_prefix='eva-browser')
LIVE = BookingSync(EVA_BROWSER_QUEUE, day_bookings)

def make_recorder():
    try:
        config = settings()
        password = get_obs_password()
        if password is None:
            return UnavailableRecorder('dual', 'Save the OBS WebSocket password in Settings on the Streaming PC')
        game = ObsWebSocket('127.0.0.1', int(config.get('obs_port', 4455)), password)
        layout = config.get('recording_layout', 'obs_game')
        if config.get('camera_backend') == 'obs' and layout == 'obs_game':
            camera_password = _keyring().get_password('EVA Recording Manager OBS Camera', 'websocket-password')
            if camera_password is None:
                return UnavailableRecorder('dual', 'Save the second OBS camera password in Settings')
            camera = ObsWebSocket('127.0.0.1', int(config.get('camera_obs_port', 4456)), camera_password)
        else:
            token = get_streamlabs_token()
            if not token:
                return UnavailableRecorder('dual', 'Save the Streamlabs Remote Control token in Settings on the Streaming PC')
            camera = build_streamlabs(token)
        return DualRecorder(game, camera, layout=layout)
    except Exception as exc:
        LOG.warning('Recorder initialization: %s', type(exc).__name__)
        return UnavailableRecorder('dual', 'Recorder setup could not be loaded. Open Settings and run checks.')

SERVICE = RecordingService(STORE, make_recorder(), settings)
QUEUE = DeliveryQueue(STORE, SERVICE)
_started = False

def start_background():
    global _started
    if _started:
        return
    _started = True
    if os.getenv('EVA_UPDATE_VALIDATING') == '1':
        return
    QUEUE.start()
    def live_monitor():
        while not QUEUE.closed.is_set():
            if not UPDATING:
                LIVE.tick()
            QUEUE.closed.wait(1)
    threading.Thread(target=live_monitor, name='eva-booking-sync', daemon=True).start()
    for row in STORE.automatic_recordings(['working']):
        if row.get('auto_state') == 'working':
            STORE.update_recording(row['id'], auto_state='pending')
    def auto_monitor():
        while not QUEUE.closed.is_set():
            if not SERVICE.active_id and not UPDATING:
                row = next((r for r in STORE.automatic_recordings(['pending','retry_wait']) if r.get('auto_retry_at',0)<=time.time()),None)
                if row:
                    automatic_delivery(row)
            QUEUE.closed.wait(1)
    threading.Thread(target=auto_monitor, name='eva-auto-delivery', daemon=True).start()
    def cleanup_monitor():
        QUEUE.closed.wait(120)
        while not QUEUE.closed.is_set():
            try:
                if not SERVICE.active_id and not UPDATING:
                    cfg = settings()
                    cleanup_old_videos(STORE, [cfg.get('game_folder'), cfg.get('camera_folder')], int(cfg.get('cleanup_days', 10) or 0), log=LOG)
            except Exception:
                LOG.exception('Old-video cleanup failed')
            QUEUE.closed.wait(3600)
    threading.Thread(target=cleanup_monitor, name='eva-cleanup', daemon=True).start()
    def monitor():
        while True:
            try:
                state, active = SERVICE.status()
                if not state.get('connected') and not active and not UPDATING:
                    repair(SERVICE, make_recorder)
            except Exception:
                LOG.exception('Recorder monitor failed')
            time.sleep(1)
    threading.Thread(target=monitor, name='eva-recorder-monitor', daemon=True).start()

_SIGNIN_WATCH = {'thread': None}


def watch_signin():
    """After 'Sign in to EVA Admin': wait for the operator to finish, then the window closes by itself and the
    connection carries on hidden in the background."""
    def run():
        deadline = time.time() + 900
        while time.time() < deadline and not QUEUE.closed.is_set():
            try:
                state = EVA_BROWSER_QUEUE.submit(signin_progress).result(timeout=120)['state']
            except Exception:
                return
            if state == 'done':
                LIVE.resume()
                EVA_STATE.update(status='connected', message='Signed in to EVA Admin. The connection now runs in the background.')
                return
            if state == 'idle':
                return
            time.sleep(2)
    thread = _SIGNIN_WATCH['thread']
    if thread is None or not thread.is_alive():
        _SIGNIN_WATCH['thread'] = threading.Thread(target=run, name='eva-signin-watch', daemon=True)
        _SIGNIN_WATCH['thread'].start()


def local(address):
    return address in {'127.0.0.1', '::1', '::ffff:127.0.0.1'}

def lan_urls(port):
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return [f'http://{a}:{port}' for a in sorted(addresses) if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback]

def automatic_delivery(recording):
    recording_id = recording['id']
    STORE.update_recording(recording_id,auto_state='working')
    try:
        if not recording.get('assigned') or recording['status']!='ready_for_review':
            raise ValueError('No complete recording with a verified booking is selected')
        selected = json.loads(recording.get('recipients_json') or '[]')
        if not selected:
            raise ValueError('No recipients selected. Videos saved; sending blocked.')
        sync = Handler._sync(None,recording)
        if not sync.get('ok') or sync.get('match')!='matched':
            raise ValueError(sync.get('message') or 'EVA booking verification failed')
        current = STORE.get_recording(recording_id)
        users = {str(u['user_id']):u for u in sync['users']}
        allowed = {r['email'].casefold():r for r in recipients_for_bookings(sync['bookings'],users)['recipients']}
        for extra in STORE.session_recipients(current):
            allowed.setdefault(extra['email'].casefold(),extra)
        chosen = {r['email'].casefold() for r in selected}
        if not chosen.issubset(allowed):
            raise ValueError('Recipient details changed in EVA Admin. Review before sending.')
        with MAINTENANCE,QUEUE.lock:
            if UPDATING:
                raise RuntimeError('Waiting for EVA update')
            QUEUE.enqueue(current,[allowed[e] for e in sorted(chosen)],[str(b['booking_id']) for b in sync['bookings']])
            STORE.update_recording(recording_id,auto_state='queued',error=None)
    except Exception as exc:
        permanent = isinstance(exc,ValueError)
        STORE.update_recording(recording_id,auto_state='needs_action' if permanent else 'retry_wait',
            auto_retry_at=time.time()+15,error=str(exc)[:500])
        LOG.warning('Automatic delivery %s: %s',recording_id,type(exc).__name__)

class Handler(BaseHTTPRequestHandler):
    server_version = 'EVA/' + VERSION

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        LOG.info('HTTP %s %s', self.command, urlparse(self.path).path)

    def _json(self, status, body):
        self._bytes(status, json.dumps(body, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _auth(self):
        if not ipaddress.ip_address(self.client_address[0]).is_private:
            self._json(403, {'error': 'EVA recording control is available on the private LAN only.'})
            return False
        host = self.headers.get('Host', '')
        allowed = {'127.0.0.1', 'localhost', socket.gethostname().casefold()}
        allowed.update(urlparse(u).hostname for u in lan_urls(self.server.server_port))
        hostname = urlparse('http://' + host).hostname
        origin = self.headers.get('Origin')
        if hostname is None or hostname.casefold() not in allowed or (origin and origin != 'http://' + host):
            self._json(403, {'error': 'Use EVA from the Streaming PC LAN address.'})
            return False
        if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + ACCESS_TOKEN):
            self._json(401, {'error': 'Reconnect to the EVA-JH Streaming PC.'})
            return False
        return True

    def _local_only(self):
        if not local(self.client_address[0]):
            raise ValueError('Open Settings on the Streaming PC for this action')

    def _body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 1_000_000 or self.headers.get('Transfer-Encoding'):
            raise ValueError('Invalid request size')
        body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        if not isinstance(body, dict):
            raise ValueError('Request must contain a JSON object')
        return body

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            return self._bytes(200, (RESOURCE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if path == '/eva.ico':
            return self._bytes(200, (RESOURCE / 'eva.ico').read_bytes(), 'image/x-icon')
        if path == '/api/health':
            return self._json(200, {'ok': True, 'version': VERSION, 'pairing_id': PAIRING_ID})
        if not self._auth():
            return
        try:
            if path == '/api/status':
                state = dict(SERVICE.cached)
                row = STORE.get_recording(SERVICE.active_id) if SERVICE.active_id else None
                if row:
                    state.update(started_at=row['started_at'] or row['created_at'], session_status=row['status'], session_error=row['error'])
                return self._json(200, {'ok': True, 'version': VERSION, 'pairing_id': PAIRING_ID,
                    'recorder': state, 'recording_layout': settings().get('recording_layout','obs_game'),
                    'venue': settings().get('venue_label') or 'Bahrain', 'admin_browser': find_browser()[0], 'eva_session_saved': admin_session_saved(),
                    'arena': (str(settings().get('arena_numbers') or '1').split(',')[0].strip() or '1'),
                    'active_recording_id': SERVICE.active_id, 'local': local(self.client_address[0]),
                    'updating': UPDATING, 'delivery_busy': QUEUE.busy(), 'setup_complete': bool(settings().get('game_folder') and settings().get('camera_folder')),
                    'eva': EVA_STATE, 'lan_urls': lan_urls(self.server.server_port),
                    'elapsed_seconds': max(0, time.time() - datetime.fromisoformat(row['started_at'] or row['created_at']).timestamp()) if row else 0})
            if path == '/api/recordings':
                rows = STORE.recent_recordings()
                jobs = {j['recording_id']: j for j in STORE.jobs()}
                for row in rows:
                    row.pop('capture_json', None)
                    row['delivery'] = {k: jobs[row['id']][k] for k in ('state', 'attempts', 'next_attempt', 'error')} if row['id'] in jobs else None
                return self._json(200, {'recordings': rows})
            if path == '/api/google/status':
                return self._json(200, sign_in_state())
            if path == '/api/eva/live':
                return self._json(200, LIVE.snapshot())
            if path == '/api/streamlabs/projectors':
                self._local_only()
                return self._json(200, {'windows':projector_windows()})
            if path == '/api/settings':
                self._local_only()
                update_result = DATA / 'updates' / 'last-result.json'
                return self._json(200, {'settings': settings(), 'version': VERSION, 'lan_urls': lan_urls(self.server.server_port),
                    'last_update': json.loads(update_result.read_text(encoding='utf-8-sig')) if update_result.is_file() else None})
            if path == '/api/diagnostics':
                self._local_only()
                return self._json(200, runtime_checks(STORE, SERVICE.obs, MODE, sign_in_state(), PROFILE_DIR))
            if path == '/api/update/check':
                self._local_only()
                manifest = settings().get('update_manifest') or os.getenv('EVA_MANAGER_UPDATE_MANIFEST', '') or DEFAULT_MANIFEST
                return self._json(200, {'configured': bool(manifest), **(check(manifest) if manifest else {'current_version': VERSION, 'message': 'Choose a local EVA update package, or save a trusted HTTPS manifest in Settings.'})})
            if path in {'/api/preview/game', '/api/preview/camera'}:
                recorder = SERVICE.obs
                target = getattr(recorder, 'game_recorder' if path.endswith('game') else 'camera_recorder', None)
                if target is None:
                    raise ObsUnavailable('Recorder preview is unavailable until setup is complete')
                return self._bytes(200, target.preview_jpeg(), 'image/jpeg')
            self._json(404, {'error': 'Not found'})
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        if not self._auth():
            return
        try:
            path = urlparse(self.path).path
            if path == '/api/update/upload':
                self._local_only()
                self.connection.settimeout(180)
                length = int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_ARCHIVE or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Choose an EVA ZIP smaller than 256 MiB')
                folder = DATA/'updates'
                folder.mkdir(parents=True,exist_ok=True)
                filename = folder/('upload-'+secrets.token_hex(8)+'.zip')
                try:
                    with filename.open('wb') as file:
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024*1024,remaining))
                            if not chunk:
                                raise ValueError('Upload interrupted. Select the ZIP again.')
                            file.write(chunk)
                            remaining -= len(chunk)
                    package = resolve_package(filename)
                    release = validate_package(package)
                    destination = folder/'chosen-update.zip'
                    import shutil
                    shutil.copy2(package,destination)
                    return self._json(200, {'ok':True,'version':release['version'],'package_path':str(destination)})
                finally:
                    filename.unlink(missing_ok=True)
            body = self._body()
            if path == '/api/recordings/start':
                with MAINTENANCE, QUEUE.lock:
                    if UPDATING:
                        raise ValueError('EVA is applying an update. Wait for restart.')
                    result = SERVICE.start(body)
                return self._json(200, {'ok': True, 'recording': result})
            if path == '/api/recordings/stop':
                with SERVICE.lock:
                    row = SERVICE.stop(str(body.get('recording_id') or ''))
                    if body.get('recipient_pending') is True and row.get('auto_send') and row['status']=='ready_for_review':
                        STORE.update_recording(row['id'],auto_state='needs_action',error='Recipient edits were not saved before Stop. Review recipients before sending.')
                        row = STORE.get_recording(row['id'])
                return self._json(200, {'ok': True, 'recording': row})
            if path == '/api/eva/reconnect':
                LIVE.resume()
                return self._json(200, {'ok': True})
            if path == '/api/recordings/assign':
                recording = self._recording(body)
                if recording['id'] != SERVICE.active_id:
                    raise ValueError('Choose a booking while recording is active')
                session = body.get('session')
                if not isinstance(session, dict):
                    raise ValueError('Select a booked session')
                result = EVA_BROWSER_QUEUE.submit(session_recipients, session).result(timeout=240)
                if not result.get('ok') or result.get('match') != 'matched':
                    raise ValueError(result.get('message') or 'Could not verify selected booking')
                recipients = recipients_for_bookings(result['bookings'],{str(u['user_id']):u for u in result['users']})
                extras = STORE.session_recipients(session)
                by_email = {r['email'].casefold():r for r in recipients['recipients']}
                for extra in extras:
                    by_email.setdefault(extra['email'].casefold(),extra)
                row = SERVICE.assign(recording['id'],session,result['bookings'],result['users'],list(by_email.values()))
                return self._json(200, {'ok':True,'recording':row,**recipients,'additional':extras,'notes':result.get('notes',[])})
            if path == '/api/recordings/recipients':
                with SERVICE.lock:
                    row = self._recording(body)
                    if row['id'] != SERVICE.active_id or not row.get('assigned') or STORE.session_key(row)!=STORE.session_key(body.get('session') or {}):
                        raise ValueError('Booking selection changed; review recipients again')
                    bookings, users = STORE.matched_data(row['id'])
                    allowed = {r['email'].casefold():r for r in recipients_for_bookings(bookings,users)['recipients']}
                    for r in STORE.session_recipients(row):
                        allowed.setdefault(r['email'].casefold(),r)
                    requested = body.get('recipient_emails')
                    if not isinstance(requested,list) or any(not isinstance(e,str) for e in requested):
                        raise ValueError('Choose recipient emails')
                    chosen = set(e.strip().casefold() for e in requested)
                    if not chosen.issubset(allowed):
                        raise ValueError('An email is not in the reviewed session')
                    STORE.update_recording(row['id'],recipients_json=json.dumps([allowed[e] for e in sorted(chosen)]))
                return self._json(200, {'ok':True})
            if path == '/api/recordings/recover':
                if body.get('confirmed') is not True:
                    raise ValueError('Confirm closing this session as incomplete')
                return self._json(200, {'ok': True, 'recording': SERVICE.recover(str(body.get('recording_id') or ''))})
            if path == '/api/recordings/manual':
                self._local_only()
                recording = self._recording(body)
                if recording['id'] == SERVICE.active_id:
                    raise ValueError('Stop the recording first')
                emails = body.get('emails')
                if isinstance(emails, str):
                    emails = re.split(r'[,;\s]+', emails)
                emails = list(dict.fromkeys(str(e).strip() for e in (emails or []) if str(e).strip()))
                if not emails or len(emails) > 10 or any(not re.fullmatch(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+', e) for e in emails):
                    raise ValueError('Enter 1 to 10 valid email addresses')
                if body.get('confirmed') is not True:
                    raise ValueError('Confirm the manual recipients')
                self._manual_booking(recording, emails, str(body.get('name') or '').strip()[:80])
                return self._json(200, {'ok': True, 'message': 'Manual recipients saved. Review and send as usual.'})
            if path == '/api/eva/open':
                self._local_only()
                if SERVICE.active_id and body.get('force') is not True:
                    raise ValueError('A recording is running. Sign in to EVA Admin after it stops, so the sign-in window cannot disturb the session.')
                result = EVA_BROWSER_QUEUE.submit(open_eva).result(timeout=70)
                EVA_STATE.update(status='login_required' if result.get('login_required') else 'browser_open', message='Sign in in the browser window. It closes by itself when you are done.')
                watch_signin()
                return self._json(200, result)
            if path == '/api/eva/signout':
                self._local_only()
                if SERVICE.active_id:
                    raise ValueError('Stop recording before signing out of EVA Admin')
                result = EVA_BROWSER_QUEUE.submit(sign_out_admin).result(timeout=60)
                LIVE.paused_login = True
                LIVE.state.update(ok=False, status='login_required', error='Signed out of EVA Admin. Use Sign in to EVA Admin in Settings.')
                EVA_STATE.update(status='login_required', message=result['message'])
                return self._json(200, result)
            if path == '/api/google/test':
                self._local_only()
                result = send_test_email(STORE)
                return self._json(200, {**result, 'message': 'Drive and Gmail work. A test email was sent to ' + result['sender'] + ' (no customer was contacted).'})
            if path == '/api/eva/verify':
                self._local_only()
                result = EVA_BROWSER_QUEUE.submit(verify_eva).result(timeout=70)
                EVA_STATE.update(status='connected' if result['ok'] else 'login_required', message=result['message'])
                if result['ok']:
                    LIVE.resume()
                return self._json(200, result)
            if path == '/api/eva/bookings':
                result = EVA_BROWSER_QUEUE.submit(day_bookings, str(body.get('session_date') or '')).result(timeout=240)
                EVA_STATE.update(status='connected' if result['ok'] else 'login_required', message=result['message'])
                return self._json(200, result)
            if path == '/api/eva/session':
                result = EVA_BROWSER_QUEUE.submit(session_recipients, body).result(timeout=240)
                recipients = recipients_for_bookings(result.get('bookings',[]), {str(u['user_id']):u for u in result.get('users',[])})
                EVA_STATE.update(status='synced' if result.get('ok') and result.get('match')=='matched' else 'needs_review', message=result['message'])
                return self._json(200, {'ok': result.get('ok') and result.get('match')=='matched', 'message':result['message'], **recipients, 'additional': STORE.session_recipients(body), 'notes': result.get('notes', [])})
            if path == '/api/recipients/draft':
                session = body.get('session')
                if not isinstance(session, dict):
                    raise ValueError('Choose a session first')
                if body.get('save') is True:
                    recipients = STORE.save_session_recipients(session, body.get('recipients'))
                else:
                    recipients = STORE.session_recipients(session)
                return self._json(200, {'ok': True, 'recipients': recipients})
            if path == '/api/eva/sync':
                recording = self._recording(body)
                result = self._sync(recording)
                return self._json(200, result)
            if path == '/api/recipients/preview':
                recording = self._recording(body)
                bookings, users = STORE.matched_data(recording['id'])
                match = match_bookings(recording, bookings)
                recipients = recipients_for_bookings(match['bookings'], users)
                return self._json(200, {'match': match['status'], **recipients, 'additional': STORE.session_recipients(recording), 'send_enabled': recording['status'] == 'ready_for_review' and match['status'] == 'matched'})
            if path == '/api/recordings/send':
                if body.get('confirmed') is not True:
                    raise ValueError('Review the recipients before queueing delivery')
                recording = self._recording(body)
                if recording['status'] != 'ready_for_review':
                    raise ValueError('This session is incomplete. Sending is blocked.')
                sync = self._sync(recording)
                if not sync.get('ok') or sync.get('match') != 'matched':
                    raise ValueError(sync.get('message', 'Refresh verified booking recipients before sending'))
                users = {str(u['user_id']): u for u in sync['users']}
                resolved = recipients_for_bookings(sync['bookings'], users)
                requested = body.get('recipient_emails')
                if not isinstance(requested, list):
                    raise ValueError('Select recipient emails')
                requested = {str(e).strip().casefold() for e in requested}
                by_email = {r['email'].strip().casefold(): r for r in resolved['recipients']}
                for extra in STORE.session_recipients(STORE.get_recording(recording['id'])):
                    by_email.setdefault(extra['email'].casefold(), extra)
                if not requested or not requested.issubset(by_email):
                    raise ValueError('Recipient list changed in EVA Admin. Sync and review it again.')
                QUEUE.enqueue(recording, [by_email[e] for e in sorted(requested)], [str(b['booking_id']) for b in sync['bookings']])
                return self._json(202, {'ok': True, 'message': 'Both videos queued. EVA will retry safe failures automatically.'})
            if path == '/api/delivery/retry':
                QUEUE.retry(str(body.get('recording_id') or ''))
                return self._json(200, {'ok': True})
            if path == '/api/settings/save':
                return self._configure(body)
            if path == '/api/recorder/repair':
                with MAINTENANCE:
                    result = repair(SERVICE, make_recorder, force=True)
                return self._json(200, {'ok': True, **result})
            if path in {'/api/google/signin', '/api/google/signout', '/api/google/open-config-folder'}:
                self._local_only()
                {'/api/google/signin': begin_sign_in, '/api/google/signout': forget_sign_in, '/api/google/open-config-folder': open_setup_folder}[path]()
                return self._json(200, {'ok': True, **sign_in_state()})
            if path == '/api/update/stage':
                return self._update(body)
            if path == '/api/update/prepare':
                self._local_only()
                self._prepare_update()
                self._json(200, {'ok': True})
                threading.Timer(1, lambda: os._exit(0)).start()
                return
            if path == '/api/bookings/import-selected':
                self._local_only()
                bookings, users = body.get('bookings'), body.get('users')
                if not isinstance(bookings, list) or not isinstance(users, list) or len(bookings)>100 or len(users)>500:
                    raise ValueError('Import selected bookings and their linked users only')
                linked = {str(uid) for b in bookings for uid in b.get('attendee_user_ids', [])} | {str(b.get('owner_user_id')) for b in bookings}
                if any(str(u['user_id']) not in linked for u in users):
                    raise ValueError('Import contains users outside the selected bookings')
                STORE.import_selected_booking_data(bookings, users)
                return self._json(200, {'ok': True})
            self._json(404, {'error': 'Not found'})
        except Exception as exc:
            self._error(exc)

    def _recording(self, body):
        row = STORE.get_recording(str(body.get('recording_id') or ''))
        if not row:
            raise ValueError('Recording not found')
        return row

    def _manual_booking(self, recording, emails, name):
        """EVA Admin is unavailable: a booking made from the recording's own times and the typed emails, clearly marked MANUAL."""
        key = 'MANUAL-' + re.sub(r'[^A-Za-z0-9]', '', str(recording['id']))[:24]
        users = [dict(user_id=f'{key}-{n}', username='', name=name or 'Manual recipient', email=e) for n, e in enumerate(emails, 1)]
        booking = dict(booking_id=key, order_id=None, branch=recording.get('branch') or 'Bahrain', arena_id=str(recording['arena_id']),
                       session_date=recording['session_date'], start_time=recording['start_time'], end_time=recording['end_time'],
                       official_session_id=recording.get('official_session_id'), status='active', game=str(recording.get('game') or ''),
                       owner_user_id=users[0]['user_id'], attendee_user_ids=[u['user_id'] for u in users])
        STORE.import_selected_booking_data([booking], users, recording['id'])
        LOG.info('Manual recipients saved for recording %s (%s addresses)', recording['id'], len(emails))

    def _manual_result(self, recording):
        bookings, users = STORE.matched_data(recording['id'])
        if bookings and all(str(b['booking_id']).startswith('MANUAL-') for b in bookings):
            return {'ok': True, 'login_required': False, 'match': 'matched', 'bookings': bookings, 'users': list(users.values()),
                    'message': 'Manual recipients (EVA Admin was not used).', 'notes': ['Recipients were typed in manually.']}
        return None

    def _sync(self, recording):
        manual = self._manual_result(recording)
        if manual:
            EVA_STATE.update(status='synced', message=manual['message'])
            return manual
        EVA_STATE.update(status='syncing', message='Reading the actual game/session and linked attendees…')
        result = EVA_BROWSER_QUEUE.submit(pull_recording_session, recording).result(timeout=180)
        if result.get('ok') and result.get('match') == 'matched':
            STORE.import_selected_booking_data(result['bookings'], result['users'], recording['id'])
            games = {b.get('game') for b in result['bookings'] if b.get('game')}
            if len(games) == 1:
                STORE.update_recording(recording['id'], game=next(iter(games)))
            EVA_STATE.update(status='synced', message=result.get('message'))
        else:
            EVA_STATE.update(status='login_required' if result.get('login_required') else 'needs_review', message=result.get('message'))
        return result

    def _configure(self, body):
        self._local_only()
        with MAINTENANCE, SERVICE.lock:
            state = SERVICE.obs.status()
            if SERVICE.active_id or state.get('recording') or UPDATING:
                raise ValueError('Stop recording before changing Settings')
            config = {}
            for key in ('game_folder', 'camera_folder', 'camera_projector_title', 'update_manifest'):
                config[key] = str(body.get(key, settings().get(key, ''))).strip()
            for key in ('game_folder', 'camera_folder'):
                if not Path(config[key]).is_dir():
                    raise ValueError('Choose existing, separate Game and Camera recording folders')
            if Path(config['game_folder']).resolve() == Path(config['camera_folder']).resolve():
                raise ValueError('Game and Camera need separate folders')
            if config['update_manifest'] and not config['update_manifest'].startswith('https://'):
                raise ValueError('Update manifest must use HTTPS')
            old = settings()
            venues = body.get('venue_names', old.get('venue_names', 'Bahrain, Marassi'))
            config['venue_names'] = ', '.join(v.strip() for v in str(venues if not isinstance(venues, list) else ', '.join(venues)).split(',') if v.strip())[:200] or 'Bahrain, Marassi'
            arenas = str(body.get('arena_numbers', old.get('arena_numbers', '1')))
            config['arena_numbers'] = ', '.join(a.strip() for a in arenas.split(',') if a.strip().isdigit())[:60] or '1'
            config['require_all_attendees'] = bool(body.get('require_all_attendees', old.get('require_all_attendees', False)))
            browser = str(body.get('browser', old.get('browser', 'auto'))).strip().lower()
            if browser not in {'auto', 'brave', 'chrome'}:
                raise ValueError('Choose Brave, Chrome, or automatic for the EVA Admin browser')
            config['browser'] = browser
            try:
                config['cleanup_days'] = int(body.get('cleanup_days', old.get('cleanup_days', 10)))
            except (TypeError, ValueError):
                raise ValueError('Days before old videos are deleted must be a number (0 = never)')
            if not 0 <= config['cleanup_days'] <= 365:
                raise ValueError('Days before old videos are deleted must be between 0 and 365')
            config['camera_backend'] = str(body.get('camera_backend', 'streamlabs'))
            if config['camera_backend'] not in {'streamlabs', 'obs'}:
                raise ValueError('Choose Streamlabs or a second OBS camera instance')
            config['recording_layout'] = str(body.get('recording_layout', settings().get('recording_layout', 'obs_game')))
            if config['recording_layout'] not in {'obs_game', 'streamlabs_game'}:
                raise ValueError('Choose the Game and Camera recorder layout')
            if config['recording_layout'] == 'streamlabs_game':
                config['camera_backend'] = 'streamlabs'  # This is the companion to the primary OBS connection.
            for key, default in (('obs_port', 4455), ('camera_obs_port', 4456)):
                config[key] = int(body.get(key) or default)
                if not 1024 <= config[key] <= 65535:
                    raise ValueError('WebSocket port must be between 1024 and 65535')
            if config['obs_port'] == config['camera_obs_port'] and config['camera_backend'] == 'obs':
                raise ValueError('The two OBS instances need different WebSocket ports')
            if body.get('obs_secret'):
                save_obs_password(str(body['obs_secret']))
            if body.get('streamlabs_secret'):
                save_streamlabs_token(str(body['streamlabs_secret']).strip())
            if body.get('camera_obs_secret'):
                _keyring().set_password('EVA Recording Manager OBS Camera', 'websocket-password', str(body['camera_obs_secret']))
            config['streamlabs_projector_title'] = str(body.get('streamlabs_projector_title', body.get('camera_projector_title', settings().get('streamlabs_projector_title', '')))).strip()
            before_browser = find_browser()[:2]
            save_settings(**config)
            if find_browser()[:2] != before_browser:
                # A different browser has its own profile: close the open one; the next sign-in uses the new one.
                EVA_BROWSER_QUEUE.submit(close_browser).result(timeout=30)
                LIVE.paused_login = False
            SERVICE.obs = make_recorder()
            # Preserve legacy mode setting, explicitly upgrade to required dual mode.
            (DATA / 'mode.txt').write_text('dual', encoding='utf-8')
        return self._json(200, {'ok': True, 'message': 'Setup saved. Credentials and settings persist across updates.'})

    def _prepare_update(self):
        global UPDATING
        with MAINTENANCE, QUEUE.lock, SERVICE.lock:
            state = SERVICE.obs.status()
            if SERVICE.active_id or state.get('recording') or QUEUE.busy() or STORE.automatic_recordings(['working']):
                raise ValueError('Stop recording and wait for current delivery before updating')
            backup = DATA / 'updates' / 'pre-update.sqlite3'
            backup.parent.mkdir(parents=True, exist_ok=True)
            STORE.backup(backup)
            UPDATING = True
            QUEUE.closed.set()

    def _update(self, body):
        self._local_only()
        manifest = settings().get('update_manifest') or os.getenv('EVA_MANAGER_UPDATE_MANIFEST', '') or DEFAULT_MANIFEST
        result = stage(manifest, str(body.get('package_path') or ''))
        if not result.get('staged'):
            return self._json(200, result)
        self._prepare_update()
        try:
            launch_update(result['package_path'], os.getpid())
        except Exception:
            global UPDATING
            UPDATING = False
            QUEUE.closed.clear()
            raise
        self._json(200, {'ok': True, 'message': 'Update validated. EVA will restart; rollback runs if validation fails.'})
        threading.Timer(1.5, lambda: os._exit(0)).start()

    def _error(self, exc):
        if isinstance(exc, (KeyError, ValueError, TypeError, UpdateError, GoogleIntegrationError, EvaPortalError)):
            self._json(400, {'error': str(exc)})
        elif isinstance(exc, ObsUnavailable):
            self._json(503, {'error': str(exc)})
        else:
            LOG.exception('EVA request failed')
            self._json(500, {'error': 'EVA could not complete this action. Open Settings → Diagnostics for details, then retry.'})

def main():
    start_background()
    server = ThreadingHTTPServer((os.getenv('EVA_MANAGER_HOST', '0.0.0.0'), int(os.getenv('EVA_MANAGER_PORT', '8765'))), Handler)
    server.serve_forever()

if __name__ == '__main__':
    main()
