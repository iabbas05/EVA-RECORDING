import os, sys, tempfile, time, unittest
sys.path.insert(0, '/home/claude/eva_v091')
TMP = tempfile.mkdtemp(prefix='eva-clean-')
os.environ.update(EVA_DATA_DIR=TMP, LOCALAPPDATA=TMP, EVA_ALLOW_INSECURE_STORE='1')
from storage import Store
from delivery_queue import cleanup_old_videos

DAY = 86400
class Clean(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(TMP, os.urandom(3).hex() + '.sqlite3')); self.store.initialize()
        self.g, self.c = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
        self.n = 0
    def rec(self, state='sent', age_days=11, drive=True, status='ready_for_review', outside=False, uploaded=(True, True)):
        self.n += 1; rid = f'r{self.n}'
        gp = os.path.join(tempfile.mkdtemp() if outside else self.g, f'{rid}-game.mp4'); cp = os.path.join(self.c, f'{rid}-cam.mp4')
        for p in (gp, cp): open(p, 'wb').write(b'x' * 10)
        self.store.create_recording(dict(id=rid, created_at='2026-10-01T10:00:00+03:00', branch='Bahrain', arena_id='81', session_date='2026-10-01',
                                         start_time='10:00:00', end_time='11:00:00', status=status, output_path=gp))
        self.store.update_recording(rid, camera_path=cp)
        for ok, p, h in zip(uploaded, (gp, cp), ('a', 'b')):
            if ok and drive: self.store.save_video_asset(rid + h, p, 'drive-' + h, 'https://d')
        if state:
            self.store.enqueue(rid, {}); self.store.job_update(rid, state=state)
            with self.store.connect() as db: db.execute('UPDATE delivery_jobs SET updated_at=? WHERE recording_id=?', (time.time() - age_days * DAY, rid))
        return rid, gp, cp
    def run_clean(self, days=10): return cleanup_old_videos(self.store, [self.g, self.c], days)

    def test_old_delivered_uploaded_videos_are_deleted(self):
        rid, gp, cp = self.rec(); out = self.run_clean()
        self.assertEqual(sorted(out), sorted([gp, cp])); self.assertFalse(os.path.exists(gp) or os.path.exists(cp))
        self.assertTrue(self.store.get_recording(rid)['files_removed_at'])
    def test_not_old_enough_is_kept(self):
        _, gp, cp = self.rec(age_days=9); self.assertEqual(self.run_clean(), []); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_exactly_the_configured_days(self):
        _, gp, _ = self.rec(age_days=3); self.assertEqual(self.run_clean(days=3) and 1, 1); self.assertFalse(os.path.exists(gp))
    def test_not_fully_sent_is_kept(self):
        for state in ('queued', 'retry_wait', 'needs_verification', 'working', None):
            _, gp, cp = self.rec(state=state); self.assertEqual(self.run_clean(), [], state); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_not_in_drive_is_kept(self):
        _, gp, cp = self.rec(drive=False); self.run_clean(); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_only_one_file_in_drive_keeps_both(self):
        _, gp, cp = self.rec(uploaded=(True, False)); self.run_clean(); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_files_outside_the_recording_folders_are_never_touched(self):
        _, gp, cp = self.rec(outside=True); self.run_clean(); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_incomplete_recordings_are_kept(self):
        _, gp, cp = self.rec(status='interrupted'); self.run_clean(); self.assertTrue(os.path.exists(gp) and os.path.exists(cp))
    def test_zero_means_never(self):
        _, gp, _ = self.rec(); self.assertEqual(self.run_clean(days=0), []); self.assertTrue(os.path.exists(gp))
    def test_runs_once_and_survives_missing_files(self):
        rid, gp, cp = self.rec(); os.remove(gp); self.run_clean()
        self.assertFalse(os.path.exists(cp)); self.assertEqual(self.run_clean(), [])
    def test_no_folders_configured_deletes_nothing(self):
        _, gp, _ = self.rec(); self.assertEqual(cleanup_old_videos(self.store, ['', None], 10), []); self.assertTrue(os.path.exists(gp))
if __name__ == '__main__': unittest.main()
