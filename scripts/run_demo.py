"""Start a local synthetic demo. No model key or live evidence is used."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
env=dict(os.environ,POKERFILL_DIAGNOSTICS_MODE='recorded',POKERFILL_HISTORY_BACKEND='sqlite',
    POKERFILL_DIAGNOSTICS_TOKEN=secrets.token_urlsafe(48),
    POKERFILL_DIAGNOSTICS_HISTORY=str(root/'artifacts/demo.sqlite3'),OPENAI_API_KEY='',PYTHONPATH=str(root/'src'))
print('Synthetic demo: http://127.0.0.1:8000/demo — no customer data or model calls.',flush=True)
raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','pokerfill_ai.service:app','--host','127.0.0.1','--port','8000'],cwd=root,env=env))
