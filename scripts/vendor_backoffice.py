"""Fetch only byte-verified, pinned domain helpers for the optional live adapter."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
pin = json.loads((ROOT/'scripts/backoffice-pin.json').read_text())
destination = ROOT/'vendor'
if all((destination/name).is_file() and hashlib.sha256((destination/name).read_bytes()).hexdigest()==digest
       for name,digest in pin['files'].items()):
    print('Pinned domain helpers already verified.')
else:
    url = f"https://github.com/{pin['repository']}/archive/{pin['commit']}.zip"
    with urllib.request.urlopen(url,timeout=60) as response:
        raw=response.read(64*1024*1024+1)
    if len(raw)>64*1024*1024:raise RuntimeError('Source archive exceeds download bound')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        prefix=archive.namelist()[0].split('/')[0]+'/'
        verified={}
        for name,digest in pin['files'].items():
            path=(destination/name).resolve()
            if not path.is_relative_to(destination.resolve()):raise RuntimeError('Invalid source path')
            member=archive.getinfo(prefix+name)
            if member.file_size>2*1024*1024:raise RuntimeError('Helper exceeds size bound')
            blob=archive.read(member)
            if hashlib.sha256(blob).hexdigest()!=digest:raise RuntimeError('Pinned helper checksum mismatch')
            verified[path]=blob
        for path,blob in verified.items():
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
    print(f"Verified {len(verified)} pinned domain helpers.")
