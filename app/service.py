"""Persistent recording state machine, including restart and partial-failure recovery."""
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from matching import BAHRAIN
from capture_files import begin_capture, resolve_files, keys_for
from obs_control import ObsUnavailable
from media_check import verify_pair

LOG = logging.getLogger('eva')
RESUME_GRACE_SECONDS = 90  # how long to wait for the recorders to reconnect after EVA restarts


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def clean_clock(value):
    value = str(value or '').strip()
    if len(value) == 5:
        value += ':00'
    datetime.strptime(value, '%H:%M:%S')
    return value

class RecordingService:
    def __init__(self, store, obs, config=None):
        self.store, self.obs = store, obs
        self.config = config or (lambda: {})
        self.lock = threading.RLock()
        unfinished = store.unfinished_recordings()
        self.active_id = unfinished[-1]['id'] if unfinished else None
        self.resume, self.resume_deadline = None, 0.0
        for row in unfinished:
            if row is unfinished[-1] and row['status'] == 'recording' and row.get('capture_json'):
                # EVA was closed while the recorders may still be recording. Re-attach to the same session
                # (Stop verifies both files against the saved start time) instead of discarding it.
                self.resume, self.resume_deadline = row['id'], time.monotonic() + RESUME_GRACE_SECONDS
                self._use_keys(json.loads(row['capture_json']).get('keys'))
                continue
            store.update_recording(row['id'], status='interrupted', error='EVA restarted during recording. Stop both and review the files before sending.')
        self.cached = {'connected': False, 'recording': False, 'mode': 'dual', 'error': 'Checking recorders…'}

    def _use_keys(self, keys):
        if hasattr(self.obs, 'set_keys'):
            self.obs.set_keys(keys or ('game', 'camera'))

    def status(self):
        with self.lock:
            state = self.obs.status()
            self.cached = state
            if self.resume and self.resume == self.active_id:
                if state.get('connected'):
                    resumed, self.resume = self.resume, None
                    if state.get('recording') and state.get('synchronized', True):
                        LOG.info('Recording %s re-attached after EVA restarted', resumed)
                    else:
                        self.store.update_recording(resumed, status='interrupted', error=(
                            'EVA restarted and the recorders were no longer both recording. Stop both and review the files before sending.'))
                elif time.monotonic() < self.resume_deadline:
                    row = self.store.get_recording(self.active_id)
                    state['started_at'] = row['started_at'] or row['created_at']
                    state['session_status'] = 'recording'
                    state['session_error'] = None
                    state['resuming'] = True
                    return state, self.active_id  # still waiting for the recorders: not a failure yet
                else:
                    resumed, self.resume = self.resume, None
                    self.store.update_recording(resumed, status='interrupted',
                                                error='The recorders did not reconnect after EVA restarted. Stop both and review the files before sending.')
            if self.active_id:
                row = self.store.get_recording(self.active_id)
                state['started_at'] = row['started_at'] or row['created_at']
                state['session_status'] = row['status']
                state['session_error'] = row['error']
                if row['status'] == 'recording' and (not state.get('connected') or not state.get('synchronized', True) or not state.get('recording')):
                    self.store.update_recording(self.active_id, status='interrupted', error=state.get('error') or 'Recording stopped outside EVA. Session is incomplete.')
                    state['session_status'] = 'interrupted'
                    state['session_error'] = self.store.get_recording(self.active_id)['error']
            return state, self.active_id

    def start(self, body):
        unassigned = body.get('unassigned') is True
        if unassigned:
            current = datetime.now(BAHRAIN)
            body = {**body, 'session_date': current.date().isoformat(), 'start_time': current.strftime('%H:%M:%S'),
                    'end_time': (current+timedelta(minutes=1)).strftime('%H:%M:%S'), 'game': '', 'official_session_id': None}
        if str(body.get('branch', 'Bahrain')).casefold() != 'bahrain' or str(body.get('arena_id', '81')) != '81':
            raise ValueError('This EVA installation is configured for Bahrain, Arena 1')
        day = str(body['session_date'])
        datetime.strptime(day, '%Y-%m-%d')
        start, end = clean_clock(body['start_time']), clean_clock(body['end_time'])
        if start == end:
            raise ValueError('Session start and end must be different')
        with self.lock:
            if self.active_id:
                raise ValueError('Stop the active session before starting another')
            cfg = self.config()
            mode = str(body.get('record_mode') or cfg.get('record_mode') or 'both')
            keys = keys_for(mode, cfg.get('recording_layout', 'obs_game'))
            self._use_keys(keys)
            current = self.obs.status()
            if not current.get('connected') or current.get('recording'):
                raise ObsUnavailable(current.get('error') or 'A recorder is already recording outside EVA')
            capture = begin_capture(cfg, keys)
            recording_id = str(uuid.uuid4())
            self.store.create_recording({'id': recording_id, 'created_at': now_utc(), 'branch': 'Bahrain', 'arena_id': '81',
                'session_date': day, 'start_time': start, 'end_time': end,
                'official_session_id': str(body.get('official_session_id') or '').strip() or None, 'status': 'starting'})
            self.store.update_recording(recording_id, capture_json=json.dumps(capture), game=str(body.get('game') or '').strip()[:120],
                                        assigned=0 if unassigned else 1, auto_send=1 if body.get('auto_send') is True else 0)
            self.active_id = recording_id
            try:
                result = self.obs.start()
                if not result.get('connected') or not result.get('recording') or not result.get('synchronized', True):
                    raise ObsUnavailable('Both recorders did not confirm recording')
            except Exception as exc:
                state = self.obs.status()
                uncertain = not state.get('connected') or state.get('recording')
                self.store.update_recording(recording_id, status='interrupted' if uncertain else 'start_failed', error=str(exc))
                if not uncertain:
                    self.active_id = None
                raise
            self.store.update_recording(recording_id, status='recording', started_at=now_utc())
            return self.store.get_recording(recording_id)

    def assign(self, recording_id, session, bookings, users, recipients):
        with self.lock:
            if recording_id != self.active_id:
                raise ValueError('Recording stopped or changed before booking selection completed')
            row = self.store.get_recording(recording_id)
            if row['status'] != 'recording':
                raise ValueError('An interrupted recording cannot be assigned or sent')
            # Validate the identity before persisting; recording clock remains unchanged.
            self.store.session_key(session)
            if clean_clock(session['start_time']) == clean_clock(session['end_time']):
                raise ValueError('Invalid booking times')
            self.store.import_selected_booking_data(bookings, users, recording_id)
            self.store.update_recording(recording_id, session_date=session['session_date'],
                start_time=clean_clock(session['start_time']), end_time=clean_clock(session['end_time']),
                game=session.get('game',''), official_session_id=session.get('official_session_id'), assigned=1,
                recipients_json=json.dumps(recipients))
            return self.store.get_recording(recording_id)

    def stop(self, recording_id):
        with self.lock:
            if not self.active_id or recording_id != self.active_id:
                raise ValueError('No matching active EVA recording was found')
            row = self.store.get_recording(recording_id)
            try:
                self.status()
                row = self.store.get_recording(recording_id)
                requested_stop = datetime.now(timezone.utc)
                result = self.obs.stop()
                if not result.get('connected') or result.get('recording'):
                    raise ObsUnavailable('Could not confirm both recorders stopped')
                paths = resolve_files(json.loads(row['capture_json']), result.get('output_paths', {}))
                elapsed = (requested_stop - datetime.fromisoformat(row['started_at'] or row['created_at'])).total_seconds()
                verify_pair({k: v for k, v in paths.items() if v}, elapsed)
            except Exception as exc:
                self.store.update_recording(recording_id, status='stop_failed', error=str(exc))
                raise
            incomplete = row['status'] == 'interrupted' or (row['error'] and row['status'] == 'stop_failed')
            self.store.update_recording(recording_id, status='incomplete' if incomplete else 'ready_for_review',
                output_path=paths['game'], camera_path=paths['camera'], stopped_at=now_utc(),
                error=row['error'] if incomplete else None)
            if row.get('auto_send'):
                self.store.update_recording(recording_id, auto_state='blocked' if incomplete else 'pending' if row.get('assigned') else 'needs_selection',
                    error=row['error'] if incomplete else None if row.get('assigned') else 'No booking selected. Videos saved; automatic delivery blocked.')
            self.active_id = None
            return self.store.get_recording(recording_id)

    def recover(self, recording_id):
        """Explicitly close a failed session only after both recorders confirm idle."""
        with self.lock:
            if recording_id != self.active_id:
                raise ValueError('No active failed session to recover')
            row = self.store.get_recording(recording_id)
            if row['status'] not in {'interrupted', 'stop_failed'}:
                raise ValueError('Use Stop for a normal recording')
            state = self.obs.stop()
            if not state.get('connected') or state.get('recording'):
                raise ObsUnavailable('Reconnect both recorders and confirm Stop before closing the failed session')
            self.store.update_recording(recording_id, status='incomplete', stopped_at=now_utc(),
                error=(row['error'] or 'Incomplete dual recording') + ' — closed after explicit operator recovery; sending blocked.')
            self.active_id = None
            return self.store.get_recording(recording_id)
