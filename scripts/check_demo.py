"""Exercise prepared demo scenarios and capture screenshots without model calls."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--url');parser.add_argument('--output',type=Path,default=ROOT/'docs/images')
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
process=None
with tempfile.TemporaryDirectory() as temporary:
    base=args.url or 'http://127.0.0.1:4460'
    if not args.url:
        env=dict(os.environ,POKERFILL_DIAGNOSTICS_MODE='recorded',POKERFILL_HISTORY_BACKEND='sqlite',
            POKERFILL_DIAGNOSTICS_HISTORY=str(Path(temporary)/'history.sqlite3'),
            POKERFILL_DIAGNOSTICS_TOKEN=secrets.token_urlsafe(48),OPENAI_API_KEY='',PYTHONPATH=str(ROOT/'src'))
        process=subprocess.Popen([sys.executable,'-m','uvicorn','pokerfill_ai.service:app','--host','127.0.0.1',
            '--port','4460','--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen(base+'/health',timeout=2):break
            except OSError:
                if process.poll() is not None:raise RuntimeError('Synthetic demo service failed to start')
                time.sleep(.2)
        else:raise RuntimeError('Demo readiness timed out')
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1150},device_scale_factor=1.5)
            errors=[];api_requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:api_requests.append(request.url) if any(p in request.url for p in ('/investigations','/history','/api/')) else None)
            page.goto(base+'/demo',wait_until='networkidle')
            page.locator('.diagnosis-summary').wait_for()
            assert 'Worker A stopped reporting' in page.locator('.diagnosis-summary').inner_text()
            assert '86 → 73 → 62 → 58 → 54' in page.locator('#diagnosis-answer').inner_text()
            assert 'Verified incident, anonymized replay' in page.locator('.demo-notice').inner_text()
            assert page.locator('.diagnosis-turn').count()==1
            page.screenshot(path=str(args.output/'investigation-desktop.png'),full_page=True)
            page.fill('#diagnosis-prompt','Was the whole PC offline, and why did the worker stop reporting?')
            page.locator('#diagnosis-form').evaluate('(form)=>form.requestSubmit()')
            page.locator('.diagnosis-turn').nth(1).wait_for()
            assert 'does not prove the entire PC shut down' in page.locator('.diagnosis-summary').last.inner_text()
            page.locator('.diagnosis-turn').last.locator('details').first.click()
            page.screenshot(path=str(args.output/'follow-up-desktop.png'),full_page=True)
            page.reload(wait_until='networkidle');page.locator('.diagnosis-turn').nth(1).wait_for()
            assert page.locator('[data-diagnosis]').count()==1
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(args.output/'follow-up-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Mobile overflow'
            page.fill('#diagnosis-prompt','Could low balances explain this too?')
            page.locator('#diagnosis-form').evaluate('(form)=>form.requestSubmit()')
            page.locator('.diagnosis-turn').nth(2).wait_for()
            assert 'Unknown funds are not the same' in page.locator('.diagnosis-summary').last.inner_text()
            page.click('#sample-restart');page.locator('.diagnosis-turn').filter(has_text='Worker 212').wait_for()
            assert 'heartbeat timeout' in page.locator('.diagnosis-summary').last.inner_text()
            page.click('#diagnosis-new');page.fill('#diagnosis-prompt','Why fewer entries? <img src=x onerror=alert(1)>')
            page.locator('#diagnosis-form').evaluate('(form)=>form.requestSubmit()')
            page.get_by_role('heading',name='Why fewer entries? <img src=x onerror=alert(1)>',exact=True).wait_for()
            assert page.locator('#diagnosis-answer img').count()==0
            assert not api_requests,'The public demo must never call the live investigation API'
            assert not errors,errors
            browser.close()
            print('PASS: ring diagnosis, contextual follow-up, reload, mobile, restart scenario, HTML escaping and no live API calls.')
    finally:
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
