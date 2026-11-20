"""Deploy asynchronously and verify the exact public revision, without printing credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--group',required=True);p.add_argument('--app',required=True);args=p.parse_args()
python=os.getenv('AZURE_CLI_PYTHON')
cli=[python,'-m','azure.cli'] if python else [shutil.which('az') or 'az']
def az(*arguments):
    result=subprocess.run([*cli,*arguments,'--output','json'],capture_output=True,text=True)
    if result.returncode:raise RuntimeError('Azure deployment operation failed; inspect Azure deployment logs')
    return json.loads(result.stdout) if result.stdout.strip() else None

subprocess.run([sys.executable,str(ROOT/'scripts/vendor_backoffice.py')],check=True)
subprocess.run([sys.executable,str(ROOT/'scripts/package_release.py')],check=True)
revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
az('webapp','deploy','--resource-group',args.group,'--name',args.app,'--src-path',str(ROOT/'artifacts/deploy.zip'),
   '--type','zip','--clean','true','--async','true','--track-status','false')
url=f'https://{args.app}.azurewebsites.net'
deadline=time.monotonic()+1200
while time.monotonic()<deadline:
    try:
        with urllib.request.urlopen(url+'/health',timeout=15) as response:data=json.load(response)
        if data.get('status')=='ready' and data.get('revision')==revision:
            print(f'Verified Azure revision {revision[:12]}: {url}',flush=True)
            break
    except (OSError,ValueError):pass
    time.sleep(10)
else:raise RuntimeError('Azure did not serve the expected revision before the verification deadline')
