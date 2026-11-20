"""Build a source-only Azure ZIP; explicit allowlist excludes credentials and history."""
from pathlib import Path
import json
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'artifacts/deploy.zip';target.parent.mkdir(exist_ok=True)
files=[ROOT/'requirements.txt',ROOT/'startup.sh']
for folder in ('src','web','vendor'):
    extensions={'.py'} if folder in ('src','vendor') else {'.html','.js','.css','.svg','.woff2'}
    files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file()
        and '__pycache__' not in p.parts and p.suffix in extensions)
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        if path.is_symlink():raise RuntimeError('Package must not contain symlinks')
        archive.write(path,path.relative_to(ROOT).as_posix())
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    archive.writestr('release.json',json.dumps({'revision':revision}))
print(f'Built artifacts/deploy.zip ({target.stat().st_size:,} bytes); no runtime data included.')
