"""python make_latest_json.py <EVA_vX_UPDATE.zip> <outdir>  -> latest.json for the GitHub repo (commit it to main)."""
import hashlib, json, os, re, sys
zip_path, out = sys.argv[1], sys.argv[2]
name = os.path.basename(zip_path); ver = re.fullmatch(r'EVA_v(\d+\.\d+\.\d+)_UPDATE\.zip', name).group(1)
sha = hashlib.sha256(open(zip_path, 'rb').read()).hexdigest()
url = f'https://github.com/iabbas05/EVA-RECORDING/releases/download/v{ver}/{name}'
os.makedirs(out, exist_ok=True)
open(os.path.join(out, 'latest.json'), 'w').write(json.dumps({'version': ver, 'package_url': url, 'sha256': sha}, indent=2) + '\n')
print(ver, sha, url)
