import os, sys, tempfile, json, hashlib, unittest
sys.path.insert(0, '/home/claude/eva_v091')
os.environ.update(EVA_DATA_DIR=tempfile.mkdtemp(), EVA_ALLOW_INSECURE_STORE='1')
import updater as U

class Gh(unittest.TestCase):
    def test_default_source_is_the_github_repo(self):
        self.assertEqual(U.DEFAULT_MANIFEST, 'https://raw.githubusercontent.com/iabbas05/EVA-RECORDING/main/latest.json')
    def test_check_reads_github_manifest(self):
        seen = []; sha = hashlib.sha256(b'x').hexdigest()
        U._open_https = lambda url, limit: (seen.append(url), json.dumps({'version': '9.9.9', 'package_url': 'https://github.com/iabbas05/EVA-RECORDING/releases/download/v9.9.9/EVA_v9.9.9_UPDATE.zip', 'sha256': sha}).encode())[1]
        r = U.check(U.DEFAULT_MANIFEST); self.assertTrue(r['available']); self.assertEqual(seen, [U.DEFAULT_MANIFEST])
    def test_bad_hash_download_is_refused(self):
        sha = hashlib.sha256(b'good').hexdigest()
        def fake(url, limit): return json.dumps({'version': '9.9.9', 'package_url': 'https://github.com/x/y/z.zip', 'sha256': sha}).encode() if url.endswith('.json') else b'tampered'
        U._open_https = fake
        with self.assertRaises(U.UpdateError): U.stage(U.DEFAULT_MANIFEST)
    def test_manager_falls_back_to_default(self):
        import manager; self.assertIs(manager.DEFAULT_MANIFEST, U.DEFAULT_MANIFEST)
if __name__ == '__main__': unittest.main()
