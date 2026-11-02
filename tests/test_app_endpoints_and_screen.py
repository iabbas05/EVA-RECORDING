import json, os, sys, tempfile, threading, unittest, urllib.request
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-app-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP, EVA_ALLOW_INSECURE_STORE='1', EVA_MANAGER_DB=os.path.join(TMP, 't.sqlite3'))
import app_paths, manager
from http.server import ThreadingHTTPServer
APP = ThreadingHTTPServer(('127.0.0.1', 0), manager.Handler); threading.Thread(target=APP.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{APP.server_address[1]}'

def call(path, method='GET', body=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer EVA-JH', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class Endpoints(unittest.TestCase):
    def test_a_status_shows_venue_and_arena(self):
        c, s = call('/api/status'); self.assertEqual(c, 200)
        self.assertEqual((s['venue'], s['arena']), ('Bahrain', '1'))
        app_paths.save_settings(venue_label='Marassi', arena_numbers='2, 3')
        s = call('/api/status')[1]; self.assertEqual((s['venue'], s['arena']), ('Marassi', '2'))
        app_paths.save_settings(arena_numbers='1')

    def test_b_settings_are_cleaned_and_saved(self):
        g, c = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
        code, out = call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, venue_names='bahrain, Marassi ,', arena_numbers='1, x, 2',
                                                            require_all_attendees=True, recording_layout='streamlabs_game'))
        self.assertEqual(code, 200, out)
        cfg = call('/api/settings')[1]['settings']
        self.assertEqual((cfg['venue_names'], cfg['arena_numbers'], cfg['require_all_attendees']), ('bahrain, Marassi', '1, 2', True))
        call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, recording_layout='streamlabs_game'))  # omitted keys keep their value
        self.assertEqual(call('/api/settings')[1]['settings']['venue_names'], 'bahrain, Marassi')
        call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, require_all_attendees=False))

    def test_b2_browser_choice_is_validated_and_reported(self):
        g, c = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
        code, out = call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, browser='firefox'))
        self.assertEqual(code, 400); self.assertIn('Brave', out['error'])
        manager.close_browser = lambda: {'ok': True}
        code, out = call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, browser='chrome')); self.assertEqual(code, 200, out)
        self.assertEqual(call('/api/settings')[1]['settings']['browser'], 'chrome'); self.assertEqual(call('/api/status')[1]['admin_browser'], 'Chrome')
        call('/api/settings/save', 'POST', dict(game_folder=g, camera_folder=c, browser='auto'))

    def test_c_google_test_and_eva_signout(self):
        manager.send_test_email = lambda store=None: {'ok': True, 'sender': 'venue@example.com', 'message_id': 'm1'}
        code, out = call('/api/google/test', 'POST', {}); self.assertEqual(code, 200)
        self.assertIn('venue@example.com', out['message']); self.assertIn('no customer', out['message'])
        manager.sign_out_admin = lambda: {'ok': True, 'message': 'Signed out of EVA Admin. The saved sign-in was removed from this PC.'}
        code, out = call('/api/eva/signout', 'POST', {}); self.assertEqual(code, 200)
        self.assertEqual(manager.LIVE.snapshot()['status'], 'login_required')
        manager.SERVICE.active_id = 'x'
        try:
            code, out = call('/api/eva/signout', 'POST', {}); self.assertEqual(code, 400); self.assertIn('Stop recording', out['error'])
        finally:
            manager.SERVICE.active_id = None

    def test_d_partial_player_notes_reach_the_screen(self):
        manager.session_recipients = lambda session: dict(ok=True, match='matched', message='ok', bookings=[dict(booking_id='1', owner_user_id='o', attendee_user_ids=[], branch='Bahrain', arena_id='81',
            session_date='2026-11-02', start_time='18:40:00', end_time='19:20:00', status='Confirmed')], users=[dict(user_id='o', name='Owner', email='o@example.com')],
            notes=['Booking 1: 1 of 10 players have an EVA account.'])
        sess = dict(session_date='2026-11-02', start_time='18:40:00', end_time='19:20:00', game='G', official_session_id=None, branch='Bahrain', arena_id='81')
        code, out = call('/api/eva/session', 'POST', sess)
        self.assertEqual(code, 200); self.assertTrue(out['ok']); self.assertIn('1 of 10', out['notes'][0])
        self.assertEqual([r['email'] for r in out['recipients']], ['o@example.com'])

class Screen(unittest.TestCase):
    def test_screen_flow(self):
        from playwright.sync_api import sync_playwright
        errors = []
        with sync_playwright() as pw:
            br = pw.chromium.launch(); page = br.new_page(); page.on('pageerror', lambda e: errors.append(str(e))); page.on('dialog', lambda d: d.accept())
            app_paths.save_settings(venue_label='Marassi', arena_numbers='1')
            page.goto(URL)
            page.wait_for_function("document.getElementById('stationVenue').textContent.startsWith('Marassi')", timeout=20000)
            self.assertEqual(page.inner_text('#stationVenue'), 'Marassi · Arena 1')
            page.click('#settingsToggle')
            page.wait_for_function("document.getElementById('venueNames').value!==''", timeout=15000)
            self.assertIn('Marassi', page.input_value('#venueNames')); self.assertEqual(page.input_value('#arenaNumbers'), '1')
            self.assertFalse(page.is_checked('#requireAll')); self.assertEqual(page.input_value('#adminBrowser'), 'auto')
            for ident in ('evaSignOut', 'googleTest'): self.assertTrue(page.is_visible('#' + ident))
            manager.send_test_email = lambda store=None: {'ok': True, 'sender': 'venue@example.com', 'message_id': 'm1'}
            page.click('#googleTest')
            page.wait_for_function("document.getElementById('accountState').textContent.includes('Drive and Gmail work')", timeout=15000)
            manager.sign_out_admin = lambda: {'ok': True, 'message': 'Signed out of EVA Admin. The saved sign-in was removed from this PC.'}
            page.click('#evaSignOut')
            page.wait_for_function("document.getElementById('accountState').textContent.includes('Signed out of EVA Admin')", timeout=15000)
            br.close()
        self.assertEqual(errors, [], errors)

def tearDownModule(): APP.shutdown()
if __name__ == '__main__': unittest.main(verbosity=1)
