import os, sys, tempfile, time, unittest, json
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-svc-'); os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP)
import service, storage
from service import RecordingService
from obs_control import ObsUnavailable

class Rec:
    """Stands in for DualRecorder."""
    def __init__(self, connected=True, recording=True, synchronized=True):
        self.s = dict(connected=connected, recording=recording, synchronized=synchronized)
        self.stopped = False
    def status(self): return dict(self.s)
    def start(self): self.s.update(connected=True, recording=True, synchronized=True); return self.status()
    def stop(self):
        self.s.update(recording=False); self.stopped = True
        return dict(self.s, output_paths={'game': self.game, 'camera': self.camera})

class Resume(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP)
        self.game, self.cam = os.path.join(self.d, 'g'), os.path.join(self.d, 'c'); os.makedirs(self.game); os.makedirs(self.cam)
        self.store = storage.Store(os.path.join(self.d, 't.sqlite3'))
        self.cfg = lambda: {'game_folder': self.game, 'camera_folder': self.cam}
        service.verify_pair = lambda paths, elapsed=0: {}
        body = {'session_date': '2026-11-02', 'start_time': '18:40:00', 'end_time': '19:20:00', 'game': 'EVA BATTLE ARENA'}
        r = Rec(recording=False); self.first = RecordingService(self.store, r, self.cfg)
        self.rid = self.first.start(body)['id']

    def files(self, rec):
        rec.game, rec.camera = os.path.join(self.game, 'g.mkv'), os.path.join(self.cam, 'c.mkv')
        open(rec.game, 'wb').write(b'\x1a\x45\xdf\xa3' + b'x' * 3000); open(rec.camera, 'wb').write(b'\x1a\x45\xdf\xa3' + b'y' * 3000)

    def test_restart_while_both_still_recording_resumes_and_stops_cleanly(self):
        rec = Rec(); self.files(rec)
        svc = RecordingService(self.store, rec, self.cfg)           # EVA restarted
        self.assertEqual(svc.active_id, self.rid)
        state, active = svc.status()
        self.assertEqual(active, self.rid); self.assertIsNone(svc.resume)
        self.assertEqual(self.store.get_recording(self.rid)['status'], 'recording')   # NOT interrupted
        row = svc.stop(self.rid)
        self.assertEqual(row['status'], 'ready_for_review'); self.assertTrue(row['camera_path'].endswith('c.mkv'))

    def test_recorders_not_recording_after_restart_means_incomplete(self):
        svc = RecordingService(self.store, Rec(recording=False), self.cfg)
        svc.status()
        row = self.store.get_recording(self.rid)
        self.assertEqual(row['status'], 'interrupted'); self.assertIn('no longer both recording', row['error'])

    def test_only_one_recorder_recording_means_incomplete(self):
        svc = RecordingService(self.store, Rec(recording=True, synchronized=False), self.cfg); svc.status()
        self.assertEqual(self.store.get_recording(self.rid)['status'], 'interrupted')

    def test_waits_for_recorders_to_reconnect_then_gives_up(self):
        service.RESUME_GRACE_SECONDS = 0.4
        try:
            rec = Rec(connected=False, recording=False)
            svc = RecordingService(self.store, rec, self.cfg)
            state, _ = svc.status()
            self.assertTrue(state.get('resuming')); self.assertEqual(self.store.get_recording(self.rid)['status'], 'recording')
            rec.s.update(connected=True, recording=True)            # recorders come back in time
            svc.status(); self.assertEqual(self.store.get_recording(self.rid)['status'], 'recording')
            # a second restart where they never come back
            svc2 = RecordingService(self.store, Rec(connected=False, recording=False), self.cfg)
            time.sleep(0.6); svc2.status()
            self.assertEqual(self.store.get_recording(self.rid)['status'], 'interrupted')
            self.assertIn('did not reconnect', self.store.get_recording(self.rid)['error'])
        finally:
            service.RESUME_GRACE_SECONDS = 90

    def test_older_unfinished_sessions_are_still_marked_interrupted(self):
        other = self.store.get_recording(self.rid)
        self.store.create_recording({'id': 'older', 'created_at': '2026-11-01T10:00:00+00:00', 'branch': 'Bahrain', 'arena_id': '81',
                                     'session_date': '2026-11-01', 'start_time': '10:00:00', 'end_time': '10:40:00', 'status': 'recording'})
        self.store.update_recording('older', capture_json='{}')
        RecordingService(self.store, Rec(), self.cfg)
        self.assertEqual(self.store.get_recording('older')['status'], 'interrupted')
        self.assertEqual(self.store.get_recording(self.rid)['status'], 'recording')

if __name__ == '__main__':
    unittest.main(verbosity=1)
