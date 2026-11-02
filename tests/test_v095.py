import json, os, sys, tempfile, threading, unittest, urllib.request
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-v95-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP, EVA_ALLOW_INSECURE_STORE='1', EVA_MANAGER_DB=os.path.join(TMP, 't.sqlite3'))
import app_paths, manager, eva_portal as P
from concurrent.futures import Future
from http.server import ThreadingHTTPServer
APP = ThreadingHTTPServer(('127.0.0.1', 0), manager.Handler); threading.Thread(target=APP.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{APP.server_address[1]}'
def call(path, method='GET', body=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer EVA-JH', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

GOOD = ['Booking Order', 'Date', 'Session', 'Game', 'Status', 'User', 'Players']
def table(headers, cells=None):
    rows = [{'cells': headers, 'booking': None, 'links': [], 'buttons': []}]
    if cells: rows.append({'cells': cells, 'booking': '123', 'links': [], 'buttons': []})
    return {'rows': rows}

class Layout(unittest.TestCase):
    def test_known_layout_is_read(self):
        rows = P._live_rows([table(GOOD, ['#1', '11/02/2026', '4:00 PM (To 5:00 PM)', 'Zombie', 'Confirmed', 'Ali', '4'])])
        self.assertEqual(len(rows), 1)
    def test_empty_day_is_not_a_layout_change(self):
        self.assertEqual(P._live_rows([table(GOOD)]), []); self.assertEqual(P._live_rows([]), [])
    def test_changed_columns_raise_layout_changed(self):
        with self.assertRaises(P.LayoutChanged) as c: P._live_rows([table(['Ref', 'When', 'Slot', 'Title', 'State', 'Who'], ['1', 'x', 'y', 'z', 'w', 'v'])])
        self.assertIn('Matching is paused', str(c.exception))
    def test_changed_date_and_times_raise_layout_changed(self):
        with self.assertRaises(P.LayoutChanged): P._live_rows([table(GOOD, ['#1', '2026-11-02', '4:00 PM (To 5:00 PM)', 'Z', 'C', 'A', '4'])])
        with self.assertRaises(P.LayoutChanged): P._live_rows([table(GOOD, ['#1', '11/02/2026', 'afternoon', 'Z', 'C', 'A', '4'])])
    def test_layout_change_is_still_an_eva_portal_error(self):
        self.assertTrue(issubclass(P.LayoutChanged, P.EvaPortalError))

class Health(unittest.TestCase):
    def run_one(self, outcome):
        clock = [1000.0]
        class Ex:
            def submit(self, fn, *a):
                f = Future()
                try: f.set_result(outcome() if callable(outcome) else outcome)
                except Exception as e: f.set_exception(e)
                return f
        sync = P.BookingSync(Ex(), lambda d: None, clock=lambda: clock[0]); sync.tick(); clock[0] += 1; sync.tick(); return sync.snapshot()
    def test_connected(self):
        self.assertEqual(self.run_one({'ok': True, 'sessions': [], 'bookings': []})['health'], 'connected')
    def test_needs_signin(self):
        self.assertEqual(self.run_one({'ok': False, 'login_required': True, 'message': 'sign in'})['health'], 'needs_signin')
    def test_changed(self):
        def boom(): raise P.LayoutChanged('EVA Admin changed (booking table). Matching is paused.')
        s = self.run_one(boom); self.assertEqual(s['health'], 'changed'); self.assertIn('Matching is paused', s['error'])
    def test_other_errors_reconnect(self):
        def boom(): raise RuntimeError('network')
        self.assertEqual(self.run_one(boom)['health'], 'reconnecting')
    def test_starts_connecting(self):
        self.assertEqual(P.BookingSync(None, None).snapshot()['health'], 'connecting')

class Manual(unittest.TestCase):
    def setUp(self):
        self.id = 'rec-' + os.urandom(3).hex()
        manager.STORE.create_recording(dict(id=self.id, created_at='2026-11-02T16:00:00+03:00', branch='Bahrain', arena_id='81', session_date='2026-11-02',
                                            start_time='16:00:00', end_time='17:00:00', status='ready_for_review'))
    def test_manual_recipients_replace_eva_admin(self):
        manager.EVA_BROWSER_QUEUE.submit = lambda *a, **k: (_ for _ in ()).throw(AssertionError('EVA Admin must not be used'))
        c, o = call('/api/recordings/manual', 'POST', dict(recording_id=self.id, emails='a@x.com, b@y.org', name='Ali', confirmed=True)); self.assertEqual(c, 200, o)
        c, o = call('/api/recipients/preview', 'POST', dict(recording_id=self.id)); self.assertEqual(c, 200, o)
        self.assertEqual(o['match'], 'matched'); self.assertEqual(sorted(r['email'] for r in o['recipients']), ['a@x.com', 'b@y.org'])
        sync = manager.Handler._sync(None, manager.STORE.get_recording(self.id)) if False else manager.Handler._manual_result(manager.Handler, manager.STORE.get_recording(self.id))
        self.assertTrue(sync['ok'] and sync['match'] == 'matched')
    def test_bad_input_is_refused(self):
        for body in (dict(emails='not-an-email', confirmed=True), dict(emails='', confirmed=True), dict(emails='a@x.com'), dict(emails=','.join(f'u{i}@x.com' for i in range(11)), confirmed=True)):
            c, o = call('/api/recordings/manual', 'POST', dict(recording_id=self.id, **body)); self.assertEqual(c, 400, (body, o))
    def test_unknown_recording_refused(self):
        self.assertEqual(call('/api/recordings/manual', 'POST', dict(recording_id='nope', emails='a@x.com', confirmed=True))[0], 400)

class CleanupSetting(unittest.TestCase):
    def test_days_are_saved_and_validated(self):
        g, c = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
        base = dict(game_folder=g, camera_folder=c, recording_layout='streamlabs_game')
        self.assertEqual(call('/api/settings/save', 'POST', dict(base, cleanup_days=7))[0], 200)
        self.assertEqual(call('/api/settings')[1]['settings']['cleanup_days'], 7)
        self.assertEqual(call('/api/settings/save', 'POST', base)[0], 200)                       # omitted keeps 7
        self.assertEqual(call('/api/settings')[1]['settings']['cleanup_days'], 7)
        for bad in ('x', -1, 400): self.assertEqual(call('/api/settings/save', 'POST', dict(base, cleanup_days=bad))[0], 400, bad)
        self.assertEqual(call('/api/settings/save', 'POST', dict(base, cleanup_days=10))[0], 200)

class RecordModeEndpoint(unittest.TestCase):
    def test_mode_is_saved_validated_and_reported(self):
        self.assertEqual(call('/api/status')[1]['record_mode'], 'both')
        for m in ('streamlabs', 'obs', 'both'):
            self.assertEqual(call('/api/recorder/mode', 'POST', dict(record_mode=m))[0], 200); self.assertEqual(call('/api/status')[1]['record_mode'], m)
        self.assertEqual(call('/api/recorder/mode', 'POST', dict(record_mode='x'))[0], 400)
    def test_mode_cannot_change_while_recording(self):
        manager.SERVICE.active_id = 'busy'
        try: self.assertEqual(call('/api/recorder/mode', 'POST', dict(record_mode='obs'))[0], 400)
        finally: manager.SERVICE.active_id = None

class SignInTiming(unittest.TestCase):
    def test_sign_in_window_is_refused_during_a_recording(self):
        manager.SERVICE.active_id = 'busy'
        try:
            c, o = call('/api/eva/open', 'POST', {}); self.assertEqual(c, 400); self.assertIn('after it stops', o['error'])
        finally: manager.SERVICE.active_id = None

if __name__ == '__main__': unittest.main()
