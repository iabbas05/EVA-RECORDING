import os, sys, tempfile, unittest, base64, email
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-g-'); os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP)
import app_paths, google_delivery as G, storage

class Http400(Exception):
    resp = type('R', (), {'status': 400})()
class Ex:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()

class Drive:
    def __init__(self, google=None, ignore=False):
        self.perms, self.creates, self.google, self.ignore, self.files_, self.deleted = {}, [], google, ignore, [], []
    def files(self): return self
    def permissions(self): return self
    def about(self): return self
    def get(self, fields): return Ex(lambda: {'user': {'emailAddress': 'venue@example.com'}})
    def list(self, **kw):
        if 'fileId' in kw:
            return Ex(lambda: {'permissions': [{'type': 'user', 'emailAddress': e} for e in self.perms.get(kw['fileId'], [])]})
        q = kw.get('q', '')
        return Ex(lambda: {'files': [{'id': f['id']} for f in self.files_ if ('evaTest' in q) == f.get('test', False)] if 'evaTest' in q else []})
    def create(self, **kw):
        if 'body' in kw and 'emailAddress' in kw['body']:
            def run():
                em = kw['body']['emailAddress']; self.creates.append((kw['fileId'], em, kw['sendNotificationEmail']))
                if self.google is not None and em not in self.google and not kw['sendNotificationEmail']: raise Http400()
                if not self.ignore: self.perms.setdefault(kw['fileId'], []).append(em)
                return {'id': 'p'}
            return Ex(run)
        def up():
            fid = f'f{len(self.files_) + 1}'; self.files_.append({'id': fid, 'test': 'evaTest' in str(kw['body'].get('appProperties', ''))})
            return {'id': fid, 'webViewLink': 'x'}
        return Ex(up)
    def delete(self, fileId): return Ex(lambda: self.deleted.append(fileId))

class Gmail:
    def __init__(self): self.sent = []
    def users(self): return self
    def messages(self): return self
    def send(self, userId, body):
        m = email.message_from_bytes(base64.urlsafe_b64decode(body['raw']))
        def run(): self.sent.append((m['To'], m['Subject'], m.get_payload(decode=True).decode())); return {'id': f'm{len(self.sent)}'}
        return Ex(run)

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP); self.store = storage.Store(os.path.join(self.d, 't.sqlite3'))
        self.g, self.m = os.path.join(self.d, 'g.mkv'), os.path.join(self.d, 'c.mkv')
        open(self.g, 'wb').write(b'game' * 500); open(self.m, 'wb').write(b'cam' * 500)
        self.drive, self.gmail = Drive(), Gmail(); G._services = lambda: (self.drive, self.gmail)
        self.rec = dict(status='ready_for_review', output_path=self.g, camera_path=self.m, session_date='2026-11-02', start_time='18:40:00', end_time='19:20:00')
        app_paths.save_settings(venue_label='Marassi')
    R = [{'email': 'a@icloud.com', 'name': 'A'}, {'email': 'b@gmail.com', 'name': 'B'}]

    def test_two_videos_one_email_each_and_venue_name(self):
        out = G.deliver_session(self.store, self.rec, self.R, ['1002'])
        self.assertEqual({o['status'] for o in out['outcomes']}, {'sent'}); self.assertEqual(len(self.gmail.sent), 2)
        body = self.gmail.sent[0][2]; self.assertIn('Game:', body); self.assertIn('Camera:', body); self.assertIn('EVA Marassi', body)

    def test_non_google_address_gets_a_drive_invitation_instead_of_failing(self):
        self.drive.google = {'b@gmail.com'}
        out = G.deliver_session(self.store, self.rec, self.R, ['1'])
        self.assertEqual({o['status'] for o in out['outcomes']}, {'sent'})
        self.assertTrue(any(c[1] == 'a@icloud.com' and c[2] is True for c in self.drive.creates))
        self.assertTrue(all(c[2] is False for c in self.drive.creates if c[1] == 'b@gmail.com'))

    def test_unconfirmed_share_blocks_the_email_and_retry_does_not_duplicate(self):
        self.drive.ignore = True
        out = G.deliver_session(self.store, self.rec, self.R, ['1'])
        self.assertEqual({o['status'] for o in out['outcomes']}, {'failed'}); self.assertEqual(self.gmail.sent, [])
        self.drive.ignore = False
        out = G.deliver_session(self.store, self.rec, self.R, ['1'])
        self.assertEqual({o['status'] for o in out['outcomes']}, {'sent'}); self.assertEqual(len(self.gmail.sent), 2)
        G.deliver_session(self.store, self.rec, self.R, ['1']); self.assertEqual(len(self.gmail.sent), 2)

    def test_connection_test_emails_only_the_signed_in_account(self):
        self.drive.files_.append({'id': 'old', 'test': True})
        out = G.send_test_email()
        self.assertTrue(out['ok']); self.assertEqual(out['sender'], 'venue@example.com')
        self.assertEqual([m[0] for m in self.gmail.sent], ['venue@example.com'])
        self.assertIn('old', self.drive.deleted)                    # the previous test file was cleaned up
        self.assertEqual(self.drive.perms, {})                      # nothing shared with anyone



class ClientFile(unittest.TestCase):
    def setUp(self):
        import google_delivery as G, tempfile, pathlib
        self.G = G; self.old = G.CLIENT_FILE
        G.CLIENT_FILE = pathlib.Path(tempfile.mkdtemp()) / 'config' / 'google_oauth_client.json'; G.CLIENT_FILE.parent.mkdir()
    def tearDown(self): self.G.CLIENT_FILE = self.old
    def put(self, name, data):
        import json; (self.G.CLIENT_FILE.parent / name).write_text(json.dumps(data)); return self.G.CLIENT_FILE.parent / name
    def test_exact_name(self):
        f = self.put('google_oauth_client.json', {'installed': {'client_id': 'x'}}); self.assertEqual(self.G.find_client_file(), f)
    def test_google_download_name_and_windows_double_extension(self):
        f = self.put('client_secret_123-abc.apps.googleusercontent.com.json', {'installed': {'client_id': 'x'}}); self.assertEqual(self.G.find_client_file(), f)
        f.unlink(); f = self.put('google_oauth_client.json.json', {'installed': {'client_id': 'x'}}); self.assertEqual(self.G.find_client_file(), f)
    def test_web_client_or_junk_is_not_accepted(self):
        self.put('web.json', {'web': {'client_id': 'x'}}); (self.G.CLIENT_FILE.parent / 'bad.json').write_text('nope')
        self.assertIsNone(self.G.find_client_file()); self.assertFalse(self.G.sign_in_state()['client_configured'])
        with self.assertRaises(self.G.GoogleIntegrationError) as c: self.G.begin_sign_in()
        self.assertIn('Desktop app', str(c.exception))


if __name__ == '__main__': unittest.main()
