import hashlib, importlib.util, json, os, shutil, sys, tempfile, unittest, zipfile
REAL_ZIP = '/home/claude/out0911/EVA_v0.9.11_UPDATE.zip'
TMP = tempfile.mkdtemp(prefix='eva-setup-t-')
src = open('/home/claude/tools/setup_main.py').read()
sha = hashlib.sha256(open(REAL_ZIP, 'rb').read()).hexdigest()
open(os.path.join(TMP, 'setup_main.py'), 'w').write(src.replace('__VERSION__', '0.9.11').replace('__SHA256__', sha))
shutil.copy(REAL_ZIP, os.path.join(TMP, 'EVA_v0.9.11_UPDATE.zip'))
os.environ.update(LOCALAPPDATA=os.path.join(TMP, 'local'), EVA_SETUP_NO_UI='1')
spec = importlib.util.spec_from_file_location('setup_main', os.path.join(TMP, 'setup_main.py')); S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)

REAL_ENSURE, REAL_RUN, REAL_BOX = S.ensure_prerequisites, S.run_installer, S.box

class Payload(unittest.TestCase):
    def test_verify_mode_checks_the_embedded_update(self):
        self.assertEqual(S.PACKAGE_NAME, 'EVA_v0.9.11_UPDATE.zip')
        self.assertEqual(S.main(['--verify']), 0)

    def test_damaged_installer_is_refused(self):
        bad = os.path.join(TMP, 'bad'); os.makedirs(bad)
        data = bytearray(open(REAL_ZIP, 'rb').read(2_000_000)); data[1000] ^= 1
        open(os.path.join(bad, S.PACKAGE_NAME), 'wb').write(bytes(data))
        with self.assertRaises(RuntimeError) as c: S.check_payload(__import__('pathlib').Path(bad) / S.PACKAGE_NAME)
        self.assertIn('damaged', str(c.exception))

    def test_unexpected_paths_in_the_update_are_refused(self):
        z = os.path.join(TMP, 'evil.zip')
        with zipfile.ZipFile(z, 'w') as f: f.writestr('../evil.txt', 'x')
        with self.assertRaises(RuntimeError): S.extract(z, tempfile.mkdtemp(dir=TMP))

class Env(unittest.TestCase):
    def test_child_programs_do_not_inherit_pyinstaller_variables(self):
        os.environ['_PYI_APPLICATION_HOME_DIR'] = 'x'; os.environ['_MEIPASS2'] = 'y'
        S.sys._MEIPASS = '/tmp/_MEI123'; os.environ['PATH'] = '/tmp/_MEI123' + os.pathsep + '/usr/bin'
        try:
            e = S.clean_env()
            self.assertFalse([k for k in e if k.startswith(('_PYI', '_MEIPASS'))])
            self.assertNotIn('_MEI123', e['PATH']); self.assertEqual(e['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        finally:
            del S.sys._MEIPASS

class Prereq(unittest.TestCase):
    def run_case(self, browser, webview, answer, install_ok=True):
        asked, installed = [], []
        S.browser_installed = lambda: browser and not installed or ('Brave.Brave' in installed and install_ok)
        S.webview2_installed = lambda: webview
        notes = REAL_ENSURE(ask=lambda t, f: (asked.append(t), answer)[1], installer=lambda pid, label: (installed.append(pid), install_ok)[1])
        return notes, asked, installed
    def test_nothing_missing_asks_nothing(self):
        notes, asked, installed = self.run_case(True, True, S.IDYES); self.assertEqual((notes, asked, installed), ([], [], []))
    def test_missing_browser_is_downloaded_after_asking(self):
        notes, asked, installed = self.run_case(False, True, S.IDYES); self.assertEqual(installed, ['Brave.Brave']); self.assertEqual(notes, [])
    def test_declining_leaves_a_note_and_downloads_nothing(self):
        notes, asked, installed = self.run_case(False, False, 7); self.assertEqual(installed, []); self.assertEqual(len(notes), 2)
    def test_failed_download_is_reported_not_hidden(self):
        notes, asked, installed = self.run_case(False, True, S.IDYES, install_ok=False); self.assertIn('Brave could not be installed', notes[0])

class Flow(unittest.TestCase):
    def setUp(self):
        S.TARGET = __import__('pathlib').Path(tempfile.mkdtemp(dir=TMP)); (S.TARGET / 'updates').mkdir()
        S.run_installer, S.box = REAL_RUN, REAL_BOX
        S.ensure_prerequisites = lambda *a, **k: []; self.boxes = []
        S.box = lambda text, flags=0, title='': (self.boxes.append(text), S.IDYES)[1]
    def test_installer_command_matches_start_here(self):
        seen = {}
        def runner(cmd, env, creationflags):
            seen.update(cmd=cmd, env=env, script=open(cmd[2]).read()); return type('R', (), {'returncode': 0})()
        folder = tempfile.mkdtemp(dir=TMP); REAL_RUN(folder, '/x/EVA_v0.9.11_UPDATE.zip', runner=runner)
        self.assertEqual(seen['cmd'][:2], ['cmd.exe', '/c'])
        self.assertIn('-File "%EVA_PS1%" -PackagePath "%EVA_ZIP%" -RecordingLayout streamlabs_game', seen['script'])
        self.assertTrue(seen['env']['EVA_PS1'].endswith('install_update.ps1')); self.assertEqual(seen['env']['EVA_ZIP'], '/x/EVA_v0.9.11_UPDATE.zip')
        self.assertIn('pause', seen['script'])                      # the window stays open on failure
    def test_success_message(self):
        S.run_installer = lambda f, p: (open(S.TARGET / 'updates' / 'last-result.json', 'w').write(json.dumps({'ok': True, 'version': '0.9.11'})), 0)[1]
        self.assertEqual(S.main([]), 0); self.assertIn('EVA 0.9.11 is installed', self.boxes[-1])
    def test_failure_message_shows_the_reason_and_old_version_is_kept(self):
        S.run_installer = lambda f, p: (open(S.TARGET / 'updates' / 'last-result.json', 'w').write(json.dumps({'ok': False, 'error': 'Stop recording first'})), 1)[1]
        self.assertEqual(S.main([]), 1); self.assertIn('Stop recording first', self.boxes[-1]); self.assertIn('previous EVA was kept', self.boxes[-1])
    def test_declining_installs_nothing(self):
        called = []
        S.run_installer = lambda f, p: called.append(1) or 0
        S.box = lambda text, flags=0, title='': 7
        self.assertEqual(S.main([]), 2); self.assertEqual(called, [])
    def test_stale_success_from_an_older_install_is_not_trusted(self):
        S.run_installer = lambda f, p: (open(S.TARGET / 'updates' / 'last-result.json', 'w').write(json.dumps({'ok': True, 'version': '0.9.0'})), 0)[1]
        self.assertEqual(S.main([]), 1)
if __name__ == '__main__': unittest.main(verbosity=1)
