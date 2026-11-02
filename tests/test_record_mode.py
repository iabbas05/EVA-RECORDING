import json, os, sys, tempfile, unittest, threading, urllib.request
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-mode-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP, EVA_ALLOW_INSECURE_STORE='1', EVA_MANAGER_DB=os.path.join(TMP, 'm.sqlite3'))
import service, storage, capture_files, google_delivery as G, delivery_queue
from dual_recorder import DualRecorder
from service import RecordingService
from obs_control import ObsUnavailable

class Fake:
    """One recorder (Streamlabs or OBS)."""
    def __init__(self, folder, name, connected=True):
        self.folder, self.name, self.connected, self.recording, self.calls = folder, name, connected, False, []
    def status(self): return dict(connected=self.connected, recording=self.recording, **({} if self.connected else {'error': self.name + ' is closed'}))
    def start(self):
        self.calls.append('start'); self.recording = True; return self.status()
    def stop(self):
        self.calls.append('stop'); self.recording = False
        self.path = os.path.join(self.folder, self.name + '.mkv'); open(self.path, 'wb').write(b'\x1a\x45\xdf\xa3' + b'x' * 3000)
        return dict(self.status(), output_path=self.path)

class Mode(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP); self.g, self.c = os.path.join(self.d, 'g'), os.path.join(self.d, 'c'); os.makedirs(self.g); os.makedirs(self.c)
        self.sl, self.obs = Fake(self.g, 'sl'), Fake(self.c, 'obs')                       # layout streamlabs_game: Streamlabs = Game, OBS = Camera
        self.rec = DualRecorder(self.obs, self.sl, 'streamlabs_game')
        self.store = storage.Store(os.path.join(self.d, 't.sqlite3'))
        self.mode = 'both'
        self.cfg = lambda: {'game_folder': self.g, 'camera_folder': self.c, 'recording_layout': 'streamlabs_game', 'record_mode': self.mode}
        service.verify_pair = lambda paths, elapsed=0: self.verified.append(dict(paths)) or {}
        self.verified = []
        self.svc = RecordingService(self.store, self.rec, self.cfg)
    def run_session(self, **body):
        row = self.svc.start(dict(unassigned=True, **body)); self.assertEqual(row['status'], 'recording')
        return self.svc.stop(row['id'])

    def test_keys_for_modes_and_layouts(self):
        k = capture_files.keys_for
        self.assertEqual(k('both', 'streamlabs_game'), ('game', 'camera'))
        self.assertEqual((k('streamlabs', 'streamlabs_game'), k('obs', 'streamlabs_game')), (('game',), ('camera',)))
        self.assertEqual((k('streamlabs', 'obs_game'), k('obs', 'obs_game')), (('camera',), ('game',)))
        with self.assertRaises(ObsUnavailable): k('nope', 'obs_game')

    def test_both_records_two_videos(self):
        row = self.run_session()
        self.assertTrue(row['output_path'].endswith('sl.mkv') and row['camera_path'].endswith('obs.mkv'))
        self.assertEqual(self.sl.calls, ['start', 'stop']); self.assertEqual(self.obs.calls, ['start', 'stop'])

    def test_streamlabs_only_never_touches_obs_even_if_obs_is_closed(self):
        self.mode = 'streamlabs'; self.obs.connected = False
        row = self.run_session()
        self.assertTrue(row['output_path'].endswith('sl.mkv')); self.assertIsNone(row['camera_path']); self.assertEqual(row['status'], 'ready_for_review')
        self.assertEqual(self.obs.calls, []); self.assertEqual(self.verified[-1].keys(), {'game'})

    def test_obs_only_never_touches_streamlabs_even_if_it_is_closed(self):
        self.mode = 'obs'; self.sl.connected = False
        row = self.run_session()
        self.assertIsNone(row['output_path']); self.assertTrue(row['camera_path'].endswith('obs.mkv'))
        self.assertEqual(self.sl.calls, [])

    def test_the_other_recorder_is_not_a_problem_while_it_is_not_selected(self):
        self.mode = 'obs'; self.sl.recording = True            # Streamlabs recording on its own: not our business
        row = self.svc.start(dict(unassigned=True)); self.assertEqual(row['status'], 'recording')

    def test_selected_recorder_closed_blocks_start(self):
        self.mode = 'obs'; self.obs.connected = False
        with self.assertRaises(ObsUnavailable): self.svc.start(dict(unassigned=True))

    def test_a_one_video_session_is_interrupted_when_its_own_recorder_stops_outside_eva(self):
        self.mode = 'streamlabs'; row = self.svc.start(dict(unassigned=True)); self.sl.recording = False
        self.svc.status(); self.assertEqual(self.store.get_recording(row['id'])['status'], 'interrupted')

    def test_restart_resumes_with_the_saved_mode(self):
        self.mode = 'streamlabs'; row = self.svc.start(dict(unassigned=True))
        rec2 = DualRecorder(self.obs, self.sl, 'streamlabs_game'); self.assertEqual(rec2.keys, ('game', 'camera'))
        RecordingService(self.store, rec2, self.cfg)
        self.assertEqual(rec2.keys, ('game',))
        self.assertEqual(rec2.status()['recording'], True)

    def test_request_can_override_the_saved_mode(self):
        self.mode = 'both'; row = self.svc.start(dict(unassigned=True, record_mode='obs')); self.assertEqual(self.sl.calls, [])
        out = self.svc.stop(row['id']); self.assertIsNone(out['output_path'])

class Delivery(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP); self.store = storage.Store(os.path.join(self.d, 'd.sqlite3'))
        self.sent, self.uploaded = [], []
        G._services = lambda: (object(), object()); G._sender_email = lambda drive: 'me@x.com'
        G._drive_video = lambda drive, path, sha, store: (self.uploaded.append(str(path)) or 'id-' + os.path.basename(str(path)))
        G._ensure_private_share = lambda drive, fid, email: 'https://drive/' + fid
        G._send_message = lambda gmail, email, name, body, label, ids, sender: (self.sent.append(body) or {'id': 'm1'})
    def video(self, name):
        p = os.path.join(self.d, name); open(p, 'wb').write(b'\x1a\x45\xdf\xa3' + name.encode() * 500); return p
    def rec(self, **paths):
        return {**dict(id='r', status='ready_for_review', session_date='2026-11-02', start_time='18:00:00', end_time='19:00:00'), **{'output_path': None, 'camera_path': None, **paths}}
    def test_both_videos_are_in_one_email(self):
        out = G.deliver_session(self.store, self.rec(output_path=self.video('g.mkv'), camera_path=self.video('c.mkv')), [{'email': 'a@x.com'}], ['1'])
        self.assertEqual(out['outcomes'][0]['status'], 'sent'); self.assertEqual(len(self.uploaded), 2)
        self.assertIn('Game: https://drive/id-g.mkv', self.sent[0]); self.assertIn('Camera: https://drive/id-c.mkv', self.sent[0])
    def test_game_only(self):
        out = G.deliver_session(self.store, self.rec(output_path=self.video('g2.mkv')), [{'email': 'a@x.com'}], ['1'])
        self.assertEqual(out['outcomes'][0]['status'], 'sent'); self.assertEqual(len(self.uploaded), 1)
        self.assertIn('Game:', self.sent[0]); self.assertNotIn('Camera', self.sent[0])
    def test_camera_only_and_not_resent(self):
        r = self.rec(camera_path=self.video('c3.mkv'))
        G.deliver_session(self.store, r, [{'email': 'a@x.com'}], ['1']); again = G.deliver_session(self.store, r, [{'email': 'a@x.com'}], ['1'])
        self.assertIn('Camera:', self.sent[0]); self.assertNotIn('Game', self.sent[0]); self.assertEqual(len(self.sent), 1)      # ledger stops a second email
        self.assertEqual(again['outcomes'][0]['status'], 'sent')
    def test_no_video_is_refused(self):
        with self.assertRaises(G.GoogleIntegrationError): G.deliver_session(self.store, self.rec(), [{'email': 'a@x.com'}], ['1'])
    def test_queue_accepts_one_video(self):
        q = delivery_queue.DeliveryQueue.__new__(delivery_queue.DeliveryQueue)
        q.store, q.lock, q.wake = self.store, threading.RLock(), threading.Event()
        r = self.rec(output_path=self.video('g4.mkv')); self.store.create_recording(dict(id='r', created_at='2026-11-02T18:00:00+03:00', branch='Bahrain', arena_id='81', session_date='2026-11-02', start_time='18:00:00', end_time='19:00:00', status='ready_for_review'))
        q.enqueue(r, [{'email': 'a@x.com'}], ['1']); job = self.store.jobs()[0]; self.assertEqual(list(json.loads(job['payload'])['hashes']), ['output_path'])
        with self.assertRaises(ValueError): q.enqueue(self.rec(), [], [])

if __name__ == '__main__': unittest.main()
