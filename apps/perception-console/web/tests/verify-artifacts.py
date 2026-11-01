"""Verify current dist, APK assets, and loopback static resource delivery."""
import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument('--offline', action='store_true', help='Skip loopback HTTP checks')
args = parser.parse_args()
web = Path(__file__).resolve().parents[1]
repo = web.parents[2]
apk = web / 'android/app/build/outputs/apk/debug/app-debug.apk'
files = [p for p in (web / 'dist').rglob('*') if p.is_file()]
bundle = [p.name for p in (web / 'dist/assets').glob('index-*.js')]
with zipfile.ZipFile(apk) as archive:
    for path in files:
        entry = 'assets/public/' + path.relative_to(web / 'dist').as_posix()
        assert archive.read(entry) == path.read_bytes(), entry
    names = archive.namelist()
    models = [n for n in names if n.endswith(('.gltf', '.glb'))]
    assert len(models) == 20, len(models)
    assert archive.read('assets/public/assets/models/smart-home-layout-2026-10-12.glb') == (web / 'public/assets/models/smart-home-layout-2026-10-12.glb').read_bytes()
    assert not any('scene-check' in n for n in names), 'Test page packaged into APK'
checks = {}
if not args.offline:
    for port, path in [(8770, '/'), (8770, '/walkthrough'), (8771, '/tablet'), (8771, '/walkthrough'),
                       (8771, '/assets/models/smart-home-layout-2026-10-12.glb'),
                       (8771, '/assets/furniture/couch_pillows.gltf'),
                       (8771, '/assets/furniture/couch_pillows.bin'),
                       (8771, '/assets/furniture/texture/furniturebits_texture.png'),
                       (8771, '/assets/furniture/kenney/kitchenFridge.glb')]:
        with urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            data = response.read()
            assert response.status == 200
            if path in ['/', '/tablet', '/walkthrough']:
                assert bundle[0].encode() in data, 'Stale HTML bundle reference'
            else:
                assert data == (web / 'dist' / path.lstrip('/')).read_bytes(), path
            checks[f'{port}{path}'] = 200
    with urlopen('http://127.0.0.1:8771/tablet/app-debug.apk', timeout=10) as response:
        assert hashlib.sha256(response.read()).digest() == hashlib.sha256(apk.read_bytes()).digest()
china = timezone(timedelta(hours=8))
result = dict(verified_at=datetime.now(china).isoformat(),
              apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
              apk_bytes=apk.stat().st_size,
              apk_built_at=datetime.fromtimestamp(apk.stat().st_mtime, china).isoformat(),
              model_types=len(models), dist_files_verified=len(files),
              build_js=bundle, http_checks=checks, apk_download_matches=not args.offline)
output = repo / 'runtime/perception/scene-final-artifacts.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
