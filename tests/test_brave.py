import os, sys, tempfile, shutil, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, '/home/claude/eva_v091'); sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='eva-brave-')
PF = os.path.join(TMP, 'Program Files'); os.makedirs(PF)
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=os.path.join(TMP, 'local'), EVA_ALLOW_INSECURE_STORE='1')
os.environ['ProgramFiles'] = PF; os.environ['ProgramFiles(x86)'] = os.path.join(TMP, 'pf86')
import mock_admin, app_paths, eva_portal as P
from playwright.sync_api import sync_playwright

with sync_playwright() as _pw:
    CHROMIUM = _pw.chromium.executable_path          # a real Chromium stands in for brave.exe
BRAVE_DIR = os.path.join(PF, 'BraveSoftware', 'Brave-Browser', 'Application'); os.makedirs(BRAVE_DIR)
BRAVE = os.path.join(BRAVE_DIR, 'brave.exe')
def script(path, body): open(path, 'w').write('#!/bin/sh\n' + body + '\n'); os.chmod(path, 0o755)
script(BRAVE, f'exec "{CHROMIUM}" --headless=new --no-sandbox --disable-gpu "$@"')                 # headless wrapper: no display needed

class Choice(unittest.TestCase):
    def test_brave_is_preferred_when_installed(self):
        app_paths.save_settings(browser='auto', browser_path='')
        self.assertEqual(P.find_browser(), ('Brave', BRAVE, P.BRAVE_PROFILE_DIR)); self.assertNotEqual(P.BRAVE_PROFILE_DIR, P.PROFILE_DIR)
    def test_settings_can_force_chrome_or_brave(self):
        app_paths.save_settings(browser='chrome'); self.assertEqual(P.find_browser(), ('Chrome', None, P.PROFILE_DIR))
        app_paths.save_settings(browser='brave'); self.assertEqual(P.find_browser()[0], 'Brave'); app_paths.save_settings(browser='auto')
    def test_falls_back_to_chrome_when_brave_is_missing(self):
        os.rename(BRAVE, BRAVE + '.off')
        try: self.assertEqual(P.find_browser(), ('Chrome', None, P.PROFILE_DIR))
        finally: os.rename(BRAVE + '.off', BRAVE)
    def test_custom_browser_path_wins(self):
        app_paths.save_settings(browser_path=BRAVE)
        try: self.assertEqual(P.find_browser()[:2], ('Brave', BRAVE))
        finally: app_paths.save_settings(browser_path='')

class Launch(unittest.TestCase):
    """The reported bug: Brave opened on about:blank."""
    def setUp(self):
        self.mock = mock_admin.Mock()
        P.BASE_URL = self.mock.url; P._is_login_page = lambda url: '/login' in url; P._window = lambda *a, **k: None
        app_paths.save_settings(browser='auto', browser_path='', venue_names='Bahrain, Marassi', arena_numbers='1')
        P._CONTEXT = None
        P.sign_out_admin()                                  # cookies are per host, not per port: forget earlier tests' sign-in
    def tearDown(self):
        try: P.sign_out_admin()
        finally: self.mock.stop()

    def test_brave_opens_eva_admin_itself_and_is_not_left_blank(self):
        page = P._page()
        self.assertNotIn(page.url, ('about:blank', '')); self.assertIn('/login', page.url)       # the mock's sign-in page
        port = P._read_cdp_state()['port']; self.assertTrue(P._cdp_alive(port))
        cmd = open(f'/proc/{P._BROWSER_PROC.pid}/cmdline', 'rb').read().decode().split('\0')
        self.assertFalse([a for a in cmd if 'enable-automation' in a])                              # a normal browser: Google sign-in is allowed
        self.assertIs(page.evaluate('navigator.webdriver'), False)
        self.assertTrue(any(a.startswith('--user-data-dir=') and 'EvaBrowserProfileBrave' in a for a in cmd))
        self.assertTrue(any(a.startswith(self.mock.url) for a in cmd))                              # the address is given to Brave itself

    def test_sign_in_read_bookings_and_keep_login_across_eva_restart(self):
        page = P._page(); page.goto(self.mock.url + '/login')                                       # operator signs in
        r = P.verify_eva(); self.assertTrue(r['ok'], r); self.assertEqual(r['branch'], 'Marassi')
        s = P.day_bookings(mock_admin.today().isoformat()); self.assertTrue(s['ok'])
        self.assertTrue(P.admin_session_saved())
        before = P._read_cdp_state()['port']
        P._CONTEXT = None                                                                           # EVA is restarted; Brave window stays open
        self.assertTrue(P.verify_eva()['ok'])
        self.assertEqual(P._read_cdp_state()['port'], before)                                       # re-attached, no second browser

    def test_login_is_restored_after_the_browser_is_closed(self):
        page = P._page(); page.goto(self.mock.url + '/login'); self.assertTrue(P.verify_eva()['ok'])
        P.close_browser(); self.assertFalse(P._cdp_alive(P._read_cdp_state().get('port', 0)))     # browser really closed
        self.assertTrue(P.admin_session_saved())
        self.assertTrue(P.verify_eva()['ok'])                                                       # new browser, saved login restored

    def test_sign_out_closes_brave_and_removes_everything(self):
        P._page(); port = P._read_cdp_state()['port']; P._save_session(P._CONTEXT, force=True)
        P.sign_out_admin()
        self.assertFalse(P._cdp_alive(port)); self.assertFalse(P.CDP_FILE.exists()); self.assertFalse(P.BRAVE_PROFILE_DIR.exists()); self.assertFalse(P.admin_session_saved())

    def test_brave_that_quits_immediately_gives_a_clear_message(self):
        quitter = os.path.join(BRAVE_DIR, 'brave_quits.exe'); script(quitter, 'exit 0')           # like Brave handing over to a window that already uses the profile
        app_paths.save_settings(browser_path=quitter)
        try:
            with self.assertRaises(P.EvaPortalError) as c: P._page()
            self.assertIn('closed straight after starting', str(c.exception))
        finally: app_paths.save_settings(browser_path='')

    def test_automatic_mode_falls_back_to_chrome_when_brave_cannot_start(self):
        calls = []
        real = P._open_context
        def fake(pw, name, exe, profile, visible=False):
            calls.append(name)
            if name == 'Brave': raise P.EvaPortalError('Brave did not become ready')
            return pw.chromium.launch_persistent_context(str(profile), headless=True)               # stands in for Google Chrome
        P._open_context = fake
        try:
            page = P._page(); self.assertEqual(calls, ['Brave', 'Chrome']); page.goto(self.mock.url + '/login'); self.assertTrue(P.verify_eva()['ok'])
        finally:
            P._open_context = real
            try: P._CONTEXT.close()
            except Exception: pass
            P._CONTEXT = None

    def test_forcing_brave_does_not_fall_back(self):
        app_paths.save_settings(browser='brave'); calls = []
        real = P._open_context
        P._open_context = lambda pw, name, exe, profile, visible=False: (calls.append(name), (_ for _ in ()).throw(P.EvaPortalError('no')))[1]
        try:
            with self.assertRaises(P.EvaPortalError): P._page()
            self.assertEqual(calls, ['Brave'])
        finally: P._open_context = real; app_paths.save_settings(browser='auto')


    def test_background_sync_does_not_keep_reopening_the_browser(self):
        """Reported: ~30 s after starting EVA, a window opened again every minute."""
        calls = []; real = P._open_context
        def failing(pw, name, exe, profile, visible=False): calls.append(name); raise P.EvaPortalError('Brave did not become ready')
        P._open_context = failing; P._LAUNCH_FAILED_AT = 0.0
        app_paths.save_settings(browser='brave')
        try:
            day = mock_admin.today().isoformat()
            for _ in range(4):                                                    # four background sync attempts, as the 1-minute retry would make
                with self.assertRaises(P.EvaPortalError) as c: P.day_bookings(day)
            self.assertEqual(len(calls), 1)                                       # only the first one tried to open a browser
            self.assertIn('Sign in to EVA Admin', str(c.exception))
            with self.assertRaises(P.EvaPortalError): P._page()                   # the operator pressing the button always tries again
            self.assertEqual(len(calls), 2)
        finally:
            P._open_context = real; P._LAUNCH_FAILED_AT = 0.0; app_paths.save_settings(browser='auto')

    def test_a_browser_that_never_becomes_ready_is_closed_not_left_blank(self):
        hang = os.path.join(BRAVE_DIR, 'brave_hangs.exe'); script(hang, 'sleep 60')
        app_paths.save_settings(browser_path=hang); P.BRAVE_START_TIMEOUT = 1; P._LAUNCH_FAILED_AT = 0.0
        try:
            with self.assertRaises(P.EvaPortalError) as c: P._page()
            self.assertIn('did not become ready', str(c.exception))
            proc = P._BROWSER_PROC
            for _ in range(30):
                if proc.poll() is not None: break
                __import__('time').sleep(0.1)
            self.assertIsNotNone(proc.poll())                                     # no stray window left behind
        finally:
            P.BRAVE_START_TIMEOUT = 30; P._LAUNCH_FAILED_AT = 0.0; app_paths.save_settings(browser_path='')

    # ---------------- the connection is hidden; a window exists only while signing in
    def test_background_connection_has_no_window(self):
        page = P._page()
        args = P._LAST_LAUNCH['args']
        self.assertEqual(P._MODE, 'hidden'); self.assertFalse(P._LAST_LAUNCH['visible'])
        self.assertIn('--headless=new', args); self.assertNotIn('--start-minimized', args)
        self.assertTrue(any(a.startswith('--window-size=') for a in args))                  # big enough that the Admin table shows every column
        self.assertNotIn(page.url, ('about:blank', ''))

    def test_sign_in_window_appears_only_when_asked_and_closes_by_itself(self):
        P._page(); P._CONTEXT.clear_cookies(); P.forget_admin_session(); hidden_port = P._read_cdp_state()['port']
        info = P.open_eva()                                                                  # the Sign in to EVA Admin button
        self.assertEqual(P._MODE, 'visible'); self.assertNotIn('--headless=new', P._LAST_LAUNCH['args'])
        self.assertFalse(P._cdp_alive(hidden_port))                                         # the hidden one was closed first (same profile)
        self.assertTrue(info['login_required'])
        self.assertEqual(P.signin_progress()['state'], 'waiting')                           # still on the sign-in page: the window stays
        self.assertEqual(P._MODE, 'visible')
        with self.assertRaises(P.EvaPortalError) as c: P.day_bookings(mock_admin.today().isoformat())   # background sync never touches the window
        self.assertIn('Finish signing in', str(c.exception))
        page = P._choose_page(P._CONTEXT); page.goto(self.mock.url + '/login'); page.goto(self.mock.url + '/bookings')   # the operator signs in
        visible_port = P._read_cdp_state()['port']
        self.assertEqual(P.signin_progress()['state'], 'done')                              # detected without any click
        self.assertEqual(P._MODE, 'hidden'); self.assertTrue(P.admin_session_saved())
        self.assertFalse(P._cdp_alive(visible_port) and visible_port == P._read_cdp_state()['port'])
        self.assertTrue(P.verify_eva()['ok'])                                               # the hidden connection works with the saved login
        self.assertEqual(P.signin_progress()['state'], 'idle')

    def test_check_connection_button_also_finishes_a_sign_in(self):
        P._page(); P._CONTEXT.clear_cookies(); P.forget_admin_session(); P.open_eva()
        r = P.verify_eva(); self.assertFalse(r['ok']); self.assertTrue(r['login_required']); self.assertIn('Finish signing in', r['message'])
        page = P._choose_page(P._CONTEXT); page.goto(self.mock.url + '/login'); page.goto(self.mock.url + '/bookings')
        r = P.verify_eva(); self.assertTrue(r['ok'], r); self.assertEqual(P._MODE, 'hidden')

    def test_pressing_sign_in_again_after_hidden_start_switches_to_a_window(self):
        P._page(); self.assertEqual(P._MODE, 'hidden')
        P.open_eva(); self.assertEqual(P._MODE, 'visible')

class Checks(unittest.TestCase):
    def test_self_check_accepts_brave_without_chrome(self):
        import diagnostics, types
        real = diagnostics.os
        diagnostics.os = types.SimpleNamespace(name='nt', environ=os.environ, getenv=os.getenv)
        try: items = {c['id']: c for c in diagnostics.install_checks()}
        finally: diagnostics.os = real
        self.assertTrue(items['chrome']['ok'], items['chrome']); self.assertIn('Brave', items['chrome']['label'])

def tearDownModule(): shutil.rmtree(TMP, ignore_errors=True)
if __name__ == '__main__': unittest.main(verbosity=2)
