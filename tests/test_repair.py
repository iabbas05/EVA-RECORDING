import os, sys, tempfile, threading, types, unittest, subprocess
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-repair-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=os.path.join(TMP, 'local'), ProgramFiles=os.path.join(TMP, 'pf'))
for rel in ('pf/obs-studio/bin/64bit/obs64.exe', 'pf/Streamlabs Desktop/Streamlabs Desktop.exe'):
    path = os.path.join(TMP, rel); os.makedirs(os.path.dirname(path)); open(path, 'w').write('x')
import recorder_repair as R

class Service:
    active_id = None; lock = threading.RLock()
    class obs:
        status = staticmethod(lambda: {'recording': False})
    config = staticmethod(lambda: {})

OBS = Service.obs

class Repair(unittest.TestCase):
    def setUp(self):
        self.clock = [10_000.0]; self.started = []; self.running = ''
        R.os = types.SimpleNamespace(name='nt', getenv=os.getenv, environ=os.environ)
        R.time = types.SimpleNamespace(monotonic=lambda: self.clock[0], sleep=lambda s: None)
        subprocess.CREATE_NO_WINDOW = 0
        R.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=self.running), CREATE_NO_WINDOW=0, DEVNULL=-3,
                                             Popen=lambda cmd, **k: self.started.append(os.path.basename(cmd[0])))
        R._hidden_startup = lambda: OBS; R._last_attempt = 0; R._last_launch.clear()

    def test_closed_recorder_apps_are_started_once_not_every_minute(self):
        self.assertEqual(sorted(R.repair(Service, lambda: OBS)['launched']), ['OBS', 'Streamlabs'])
        for _ in range(8):                                  # every minute for 8 minutes, apps still not detected as running
            self.clock[0] += 61; R.repair(Service, lambda: OBS)
        self.assertEqual(sorted(self.started), ['Streamlabs Desktop.exe', 'obs64.exe'])    # still only the first launch
        self.clock[0] += 700                                                               # after 10 minutes it may help again
        R.repair(Service, lambda: OBS); self.assertEqual(len(self.started), 4)

    def test_running_apps_are_never_started_and_manual_reconnect_always_works(self):
        self.running = '"obs64.exe","1"\n"Streamlabs Desktop.exe","2"\n'
        self.assertEqual(R.repair(Service, lambda: OBS)['launched'], []); self.assertEqual(self.started, [])
        self.running = ''; R.repair(Service, lambda: OBS); n = len(self.started)
        self.clock[0] += 61; R.repair(Service, lambda: OBS, force=True)                     # the Reconnect button
        self.assertEqual(len(self.started), n + 2)

if __name__ == '__main__': unittest.main()
