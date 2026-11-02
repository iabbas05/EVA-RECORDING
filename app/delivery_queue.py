"""Durable consent-scoped delivery jobs with backoff and conservative Gmail retries."""
import json
import logging
import threading
import time
from google_delivery import deliver_session, video_fingerprint

LOG = logging.getLogger('eva')
BUSY_STATES = {'uploading_game', 'uploading_camera', 'sharing', 'emailing', 'working'}

class DeliveryQueue:
    def __init__(self, store, service, deliver=deliver_session):
        self.store, self.service, self.deliver = store, service, deliver
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.current = None
        for job in store.jobs():
            if job['state'] in BUSY_STATES:
                store.job_update(job['recording_id'], state='queued', error='Resuming interrupted delivery')

    def enqueue(self, recording, recipients, booking_ids):
        if recording['status'] != 'ready_for_review' or not recording.get('camera_path'):
            raise ValueError('This session is incomplete or has not stopped; sending is blocked')
        with self.lock:
            hashes = {key: video_fingerprint(recording[key])[1] for key in ('output_path', 'camera_path')}
            self.store.enqueue(recording['id'], {'recipients': recipients, 'booking_ids': booking_ids, 'hashes': hashes})
            self.wake.set()
        return self.store.jobs()

    def retry(self, recording_id):
        with self.lock:
            job = next((j for j in self.store.jobs() if j['recording_id'] == recording_id), None)
            if not job or job['state'] not in {'retry_wait', 'needs_action'}:
                raise ValueError('This job cannot be retried automatically. Uncertain Gmail messages need manual verification.')
            self.store.job_update(recording_id, state='queued', next_attempt=0, error=None)
            self.wake.set()

    def busy(self):
        return self.current is not None

    def run_once(self):
        with self.lock:
            if self.service.active_id:
                return False
            job = next((j for j in reversed(self.store.jobs()) if j['state'] in {'queued', 'retry_wait'} and j['next_attempt'] <= time.time()), None)
            if not job:
                return False
            recording_id = job['recording_id']
            self.current = recording_id
            attempts = job['attempts'] + 1
            self.store.job_update(recording_id, state='working', attempts=attempts, error=None)
        try:
            recording = self.store.get_recording(recording_id)
            payload = json.loads(job['payload'])
            for key, digest in payload['hashes'].items():
                if video_fingerprint(recording[key])[1] != digest:
                    raise ValueError('A recording file changed after recipient confirmation. Sending is blocked.')
            result = self.deliver(self.store, recording, payload['recipients'], payload['booking_ids'],
                                  lambda state: self.store.job_update(recording_id, state=state))
            states = {r['status'] for r in result['outcomes']}
            if 'uncertain' in states:
                self.store.job_update(recording_id, state='needs_verification', error=json.dumps(result['outcomes'], ensure_ascii=False))
            elif 'failed' in states:
                raise RuntimeError(json.dumps(result['outcomes'], ensure_ascii=False))
            elif states == {'sent'}:
                self.store.job_update(recording_id, state='sent', error=None)
            else:
                self.store.job_update(recording_id, state='needs_verification', error='Delivery returned no confirmed sent messages')
        except Exception as exc:
            LOG.warning('Delivery %s failed: %s', recording_id, type(exc).__name__)
            text = str(exc)
            permanent = isinstance(exc, (ValueError, FileNotFoundError)) or any(term in text.casefold() for term in
                ('sign in', 'sign-in expired', 'invalid_grant', 'malformed', 'status 400', 'status 401', 'status 403', 'httpError 403'.casefold()))
            self.store.job_update(recording_id, state='needs_action' if permanent else 'retry_wait',
                                  next_attempt=time.time() + min(900, 10 * 2 ** min(attempts, 7)), error=text[:1500])
        finally:
            with self.lock:
                self.current = None
        return True

    def start(self):
        def worker():
            while not self.closed.is_set():
                if not self.run_once():
                    self.wake.wait(5)
                    self.wake.clear()
        threading.Thread(target=worker, name='eva-delivery', daemon=True).start()


def cleanup_old_videos(store, folders, days, now=None, log=None):
    """Delete the local Game/Camera files of recordings that are fully delivered and safely in Drive, `days` after delivery.

    Only a file that (1) belongs to a recording whose delivery job is 'sent', (2) is recorded in video_assets with a Drive file id
    under that exact path, and (3) sits inside the configured recording folders is removed. Database rows and the Drive copies stay."""
    from datetime import datetime, timezone
    from pathlib import Path
    if not days or days <= 0:
        return []
    now = time.time() if now is None else now
    roots = []
    for folder in folders:
        try:
            if folder:
                roots.append(Path(folder).resolve())
        except OSError:
            pass
    if not roots:
        return []

    def norm(value):
        try:
            return str(Path(value).resolve())
        except OSError:
            return None
    with store.connect() as db:
        assets = {norm(r['local_path']) for r in db.execute("SELECT local_path, drive_file_id FROM video_assets") if r['drive_file_id']}
        recordings = [dict(r) for r in db.execute("SELECT * FROM recordings WHERE files_removed_at IS NULL AND status='ready_for_review'")]
    jobs = {j['recording_id']: j for j in store.jobs()}
    removed = []
    for rec in recordings:
        job = jobs.get(rec['id'])
        if not job or job['state'] != 'sent' or now - float(job['updated_at']) < days * 86400:
            continue
        paths = [rec.get('output_path'), rec.get('camera_path')]
        if not all(paths):
            continue
        resolved = [norm(p) for p in paths]
        if any(r is None or r not in assets for r in resolved):
            continue                                                # not confirmed in Drive: keep the files
        files = [Path(p) for p in paths]
        if any(f.is_symlink() or not any(Path(r).is_relative_to(root) for root in roots) for f, r in zip(files, resolved)):
            continue
        try:
            for f in files:
                if f.exists():
                    f.unlink()
                    removed.append(str(f))
            store.update_recording(rec['id'], files_removed_at=datetime.now(timezone.utc).isoformat())
            if log:
                log.info('Removed local videos of %s (delivered and in Drive)', rec['id'])
        except OSError as exc:
            if log:
                log.warning('Could not remove local videos of %s: %s', rec['id'], type(exc).__name__)
    return removed
