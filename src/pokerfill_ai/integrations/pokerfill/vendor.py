"""Activate the pinned optional Backoffice domain helpers for live evidence reads."""
import os,sys
from pathlib import Path
def activate():
    root=Path(os.environ.get('POKERFILL_BACKOFFICE_API',Path(__file__).resolve().parents[4]/'vendor/api'))
    if not (root/'backoffice/store.py').is_file():
        raise RuntimeError('Install pinned Backoffice helpers with scripts/vendor_backoffice.py for live mode')
    sys.path.insert(0,str(root))
