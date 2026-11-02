"""Runs the real eva_portal.py against a mock that copies the real admin.eva.gg page structure."""
import os, sys, tempfile, shutil, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, '/home/claude/eva_v091'); sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='eva-t-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP, EVA_ALLOW_INSECURE_STORE='1')
import mock_admin
from datetime import timedelta
import app_paths, eva_portal as P
from playwright.sync_api import sync_playwright

MOCK = mock_admin.Mock()
PW = sync_playwright().start()
CTX = PW.chromium.launch_persistent_context(os.path.join(TMP, 'profile'), headless=True)
P.BASE_URL = MOCK.url
P._is_login_page = lambda url: '/login' in url
P._CONTEXT = CTX
P._window = lambda *a, **k: None
page = CTX.new_page(); page.goto(MOCK.url + '/login'); page.close()    # the operator signs in (mock sets the cookie)

def session(start, end, game='EVA BATTLE ARENA', day=None):
    return dict(session_date=(day or mock_admin.today()).isoformat(), start_time=start, end_time=end, game=game,
                official_session_id=None, branch='Bahrain', arena_id='81')

class Live(unittest.TestCase):
    def setUp(self):
        app_paths.save_settings(venue_names='Bahrain, Marassi', arena_numbers='1', require_all_attendees=False)
        MOCK.scope = 'Marassi'

    def test_01_verify_reads_venue_from_scope_button(self):
        r = P.verify_eva()
        self.assertTrue(r['ok'], r); self.assertEqual(r['branch'], 'Marassi'); self.assertIn('Marassi', r['message'])
        self.assertEqual(app_paths.settings()['venue_label'], 'Marassi')
        self.assertTrue(P.admin_session_saved())

    def test_02_other_venue_is_refused(self):
        MOCK.scope = 'Dubai'
        with self.assertRaises(P.EvaPortalError) as c: P.verify_eva()
        self.assertIn('Dubai', str(c.exception))
        app_paths.save_settings(venue_names='Dubai')
        self.assertTrue(P.verify_eva()['ok'])   # allowed once the operator adds it

    def test_03_day_list_pages_and_cancelled(self):
        r = P.day_bookings(mock_admin.today().isoformat())
        self.assertTrue(r['ok'], r)
        ids = {b['booking_id'] for b in r['bookings']}
        self.assertIn('2011', ids)                       # a row from page 2 was read
        self.assertNotIn('1099', ids)                    # yesterday is not today
        starts = [(s['start_time'], s['end_time'], s['booking_count']) for s in r['sessions']]
        self.assertIn(('19:20:00', '20:00:00', 2), starts)              # two bookings, one play session
        self.assertNotIn('14:00:00', [s['start_time'] for s in r['sessions']])   # cancelled booking excluded

    def test_04_past_day_uses_last_30_days(self):
        y = mock_admin.today() - timedelta(days=1)
        r = P.day_bookings(y.isoformat())
        self.assertTrue(r['ok']); self.assertEqual([b['booking_id'] for b in r['bookings']], ['1099'])

    def test_05_recipients_for_shared_session(self):
        r = P.session_recipients(session('19:20:00', '20:00:00'))
        self.assertTrue(r['ok'], r); self.assertEqual(r['match'], 'matched')
        self.assertEqual(sorted(b['booking_id'] for b in r['bookings']), ['1003', '1004'])
        emails = {u['user_id']: u['email'] for u in r['users']}
        self.assertEqual(emails, {'u1': 'u1@example.com', 'u2': 'u2@example.com', 'u3': 'u3@example.com', 'u4': 'u4@example.com',
                                  'own1': 'owner1@example.com', 'own2': ''})
        self.assertEqual(len(r['notes']), 1); self.assertIn('1004', r['notes'][0]); self.assertIn('1 of 5', r['notes'][0])

    def test_06_one_of_ten_linked_is_allowed_and_user_id_with_hash(self):
        r = P.session_recipients(session('18:40:00', '19:20:00'))
        self.assertTrue(r['ok'], r); self.assertEqual(r['bookings'][0]['attendees_linked'], 1)
        self.assertEqual(r['bookings'][0]['attendees_total'], 10)
        self.assertIn('MAH#33769', {u['user_id'] for u in r['users']})
        self.assertIn('hash@example.com', {u['email'] for u in r['users']})
        self.assertIn('1 of 10', r['notes'][0])

    def test_07_strict_mode_restores_old_behaviour(self):
        app_paths.save_settings(require_all_attendees=True)
        with self.assertRaises(P.EvaPortalError) as c: P.session_recipients(session('18:40:00', '19:20:00'))
        self.assertIn('Not every player', str(c.exception))

    def test_08_other_arena_blocked_until_configured(self):
        with self.assertRaises(P.EvaPortalError) as c: P.session_recipients(session('20:00:00', '20:40:00'))
        self.assertIn('arena', str(c.exception))
        app_paths.save_settings(arena_numbers='1, 2')
        self.assertTrue(P.session_recipients(session('20:00:00', '20:40:00'))['ok'])

    def test_09_wrong_time_is_no_match(self):
        r = P.session_recipients(session('03:00:00', '03:40:00'))
        self.assertTrue(r['ok']); self.assertEqual(r['match'], 'no_match'); self.assertEqual(r['users'], [])

    def test_10_signin_survives_losing_session_cookies(self):
        P._save_session(CTX, force=True)
        self.assertTrue(P.admin_session_saved())
        CTX.clear_cookies()
        self.assertFalse([c for c in CTX.cookies() if c['name'] == 'evasess'])
        P._restore_session(CTX)
        self.assertTrue([c for c in CTX.cookies() if c['name'] == 'evasess'])
        self.assertTrue(P.verify_eva()['ok'])
        # a cookie the browser still has is never overwritten with an older saved copy
        CTX.add_cookies([{'name': 'evasess', 'value': 'NEWER', 'url': MOCK.url}])
        P._restore_session(CTX)
        self.assertEqual([c['value'] for c in CTX.cookies() if c['name'] == 'evasess'], ['NEWER'])
        CTX.add_cookies([{'name': 'evasess', 'value': 'SECRETVALUE123', 'url': MOCK.url}])

    def test_11_expired_session_asks_to_sign_in(self):
        MOCK.valid = False
        try:
            r = P.day_bookings(mock_admin.today().isoformat())
            self.assertFalse(r['ok']); self.assertTrue(r['login_required'])
            r = P.session_recipients(session('19:20:00', '20:00:00'))
            self.assertFalse(r['ok']); self.assertTrue(r['login_required'])
        finally:
            MOCK.valid = True

    def test_99_sign_out_removes_everything(self):
        P._save_session(CTX, force=True); self.assertTrue(P.admin_session_saved())
        r = P.sign_out_admin()
        self.assertTrue(r['ok']); self.assertFalse(P.admin_session_saved()); self.assertIsNone(P._CONTEXT)

def tearDownModule():
    try: PW.stop()
    except Exception: pass
    MOCK.stop(); shutil.rmtree(TMP, ignore_errors=True)

if __name__ == '__main__':
    unittest.main(verbosity=2)
