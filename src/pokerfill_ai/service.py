"""Server-authenticated diagnosis API. The public demo uses synthetic browser-only data."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .agent import investigate
from .data import Data
from .conversations import MAX_TURNS, context

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv('POKERFILL_DIAGNOSTICS_HISTORY', str(ROOT/'artifacts/diagnostics/history.sqlite3')))
TOKEN = os.getenv('POKERFILL_DIAGNOSTICS_TOKEN', '')
MODE = os.getenv('POKERFILL_DIAGNOSTICS_MODE', 'live')


class Scope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer_id: int = Field(gt=0)
    site_id: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=200)

    def owner(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


class Inquiry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Scope
    prompt: str = Field(min_length=3, max_length=2000)
    request_id: uuid.UUID
    parent_id: uuid.UUID | None = None


@contextmanager
def connection():
    if os.getenv('POKERFILL_HISTORY_BACKEND','sqlite')=='azure-sql':
        from .history_sql import connection as sql_connection
        with sql_connection() as c:yield c
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with c: yield c
    finally:
        c.close()


def init():
    with connection() as c:
        if os.getenv('POKERFILL_HISTORY_BACKEND','sqlite')=='azure-sql':
            c.execute('SELECT TOP(1) id FROM investigations').fetchall()
        else:
            c.execute('''CREATE TABLE IF NOT EXISTS investigations(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, prompt TEXT NOT NULL,
                status TEXT NOT NULL, created_utc TEXT NOT NULL, result TEXT, error TEXT)''')
            columns={r['name'] for r in c.execute('PRAGMA table_info(investigations)')}
            for name,kind in (('parent_id','TEXT'),('root_id','TEXT'),('turn_index','INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:c.execute(f'ALTER TABLE investigations ADD COLUMN {name} {kind}')
            c.execute('UPDATE investigations SET root_id=id WHERE root_id IS NULL')
            c.execute('CREATE INDEX IF NOT EXISTS ix_owner ON investigations(owner,created_utc)')
            c.execute('CREATE INDEX IF NOT EXISTS ix_owner_root ON investigations(owner,root_id,turn_index)')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_one_reply ON investigations(parent_id) WHERE parent_id IS NOT NULL')
        c.execute("UPDATE investigations SET status='failed',error='Diagnosis service restarted; submit a new request.' WHERE status IN ('queued','running')")
        c.execute('''DELETE FROM investigations WHERE root_id IN
            (SELECT root_id FROM investigations GROUP BY root_id HAVING MAX(created_utc)<?)''',
            (datetime.fromtimestamp(time.time()-90*86400,timezone.utc).isoformat(),))


def thread(c, owner, root_id):
    return c.execute('SELECT * FROM investigations WHERE owner=? AND root_id=? ORDER BY turn_index LIMIT ?',
                     (owner,root_id,MAX_TURNS)).fetchall()


def public_turn(row, full=False):
    result=json.loads(row['result']) if row['result'] else None
    if result and not full:
        result={k:result.get(k) for k in ('report','usage','mode','history_context')}
        result['evidence_partial']=True
    return {k:row[k] for k in ('id','parent_id','root_id','turn_index','prompt','status','created_utc','error')} | {'result':result}


def authorize(authorization):
    if len(TOKEN) < 32 or not hmac.compare_digest(authorization or '', 'Bearer '+TOKEN):
        raise HTTPException(401, 'Private diagnostic broker authentication required')


def failure_message(error):
    # Only classify allowlisted provider metadata; never expose raw messages,
    # request bodies, API keys or connection details.
    body=getattr(error,'body',None)
    body=body if isinstance(body,dict) else {}
    if isinstance(body.get('error'),dict):body=body['error']
    code=body.get('code') or getattr(error,'code',None)
    if code in ('credit_balance_exhausted','insufficient_quota') or body.get('type')=='insufficient_quota':
        return 'OpenAI API credits are exhausted. Replenish credits for the configured API account, then retry this question. Earlier answers are saved.'
    if getattr(error,'status_code',None)==429:
        return 'The model provider rate limit was reached. Wait briefly, then retry this question. Earlier answers are saved.'
    if getattr(error,'status_code',None) in (401,403):
        return 'Model API access was rejected. Check the configured API key and project permissions, then retry.'
    if type(error) is RuntimeError and str(error) in ('Investigation token budget reached','Investigation time budget reached'):
        return str(error)+'. Narrow the question or start a new investigation. Earlier answers are saved.'
    return 'Investigation could not finish. No diagnosis was produced. Check service configuration and retry.'


class RecordedData:
    def __init__(self, scope): self.scope = scope
    def read(self, name, hours=24, tournament='', game_format='RING'):
        values = {'get_schedule_compliance': {'targets': {'RING': 70, 'SNG': 0, 'MTT': 0}, 'entries': [{'format': 'RING', 'status': 'seated', 'count': 0}]},
                  'get_machine_health': [{'MachineId': 5, 'IsReady': True, 'LastHeartbeatUtc': '2026-11-19T15:00:00Z'}],
                  'get_lobby_snapshot': [{'ObservedUtc': '2026-11-19T05:12:00Z', 'TableType': 'RING'}]}
        return {'source': name, 'captured_utc': '2026-11-19T15:00:00Z', 'site_id': self.scope.site_id,
                'coverage': 'Recorded demonstration fixture, not live site data', 'data': values.get(name, {'available': False})}


async def worker(queue):
    while True:
        inquiry = await queue.get()
        rid = str(inquiry.request_id)
        try:
            with connection() as c: c.execute("UPDATE investigations SET status='running' WHERE id=?", (rid,))
            data = RecordedData(inquiry.scope) if MODE == 'recorded' else Data(inquiry.scope)
            if MODE=='live' and os.getenv('POKERFILL_DIAGNOSTICS_COLLECT_LOGS')=='1':
                try:await asyncio.to_thread(data.wait_for_logs)
                except Exception:logging.getLogger(__name__).warning('Log preflight unavailable; continuing database diagnosis')
            with connection() as c:
                current=c.execute('SELECT root_id,turn_index FROM investigations WHERE id=? AND owner=?',
                                  (rid,inquiry.scope.owner())).fetchone()
                previous=[r for r in thread(c,inquiry.scope.owner(),current['root_id']) if r['turn_index']<current['turn_index']]
            result = await asyncio.to_thread(investigate, inquiry.prompt, data, mode=MODE,
                                            history=context(previous),history_total=len(previous))
            with connection() as c:
                c.execute("UPDATE investigations SET status='completed',result=? WHERE id=?", (json.dumps(result, default=str), rid))
        except Exception as error:
            logging.getLogger(__name__).warning('Investigation failed (%s); sensitive exception details suppressed', type(error).__name__)
            with connection() as c:
                c.execute("UPDATE investigations SET status='failed',error=? WHERE id=?", (failure_message(error), rid))
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(app):
    if len(TOKEN) < 32: raise RuntimeError('Configure a private broker token of at least 32 characters')
    if MODE not in ('live', 'recorded'): raise RuntimeError('Invalid diagnostic mode')
    if MODE == 'live' and not os.getenv('OPENAI_API_KEY'): raise RuntimeError('Configure OPENAI_API_KEY or explicitly select recorded mode')
    init()
    app.state.queue = asyncio.Queue(maxsize=10)
    task = asyncio.create_task(worker(app.state.queue))
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protect_private_api(request, call_next):
    public=request.method in ('GET','HEAD') and (request.url.path in ('/','/demo','/health') or request.url.path.startswith('/assets/'))
    if not public:
        try:authorize(request.headers.get('authorization',''))
        except HTTPException:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Server authentication required'},status_code=401)
    return await call_next(request)


@app.get('/health')
def health(): return {'status': 'ready', 'mode': MODE}


@app.post('/investigations', status_code=202)
async def create(body: Inquiry, authorization: str = Header(default='')):
    authorize(authorization)
    with connection() as c:
        old = c.execute('SELECT * FROM investigations WHERE id=?', (str(body.request_id),)).fetchone()
        if old:
            if old['owner'] != body.scope.owner(): raise HTTPException(404, 'Request not found')
            if old['prompt'] != body.prompt or old['parent_id'] != (str(body.parent_id) if body.parent_id else None):
                raise HTTPException(409, 'Request ID already used')
            return {'id': old['id'], 'status': old['status'], 'root_id':old['root_id'], 'mode': MODE}
        if app.state.queue.full(): raise HTTPException(429, 'Diagnostic queue is full; try again later')
        root_id,turn_index,parent_id=str(body.request_id),0,None
        if body.parent_id:
            parent_id=str(body.parent_id)
            parent=c.execute('SELECT * FROM investigations WHERE id=? AND owner=?',(parent_id,body.scope.owner())).fetchone()
            if not parent:raise HTTPException(404,'Request not found')
            turns=thread(c,body.scope.owner(),parent['root_id'])
            if turns[-1]['id']!=parent_id or parent['status'] not in ('completed','failed'):
                raise HTTPException(409,'Open the latest answer before asking a follow-up')
            if len(turns)>=MAX_TURNS:raise HTTPException(409,'This investigation has reached its question limit; start a new investigation')
            root_id,turn_index=parent['root_id'],parent['turn_index']+1
        pending = c.execute("SELECT COUNT(*) FROM investigations WHERE owner=? AND status IN ('queued','running')", (body.scope.owner(),)).fetchone()[0]
        recent = c.execute("SELECT COUNT(*) FROM investigations WHERE owner=? AND created_utc>=?", (body.scope.owner(), datetime.fromtimestamp(time.time()-3600, timezone.utc).isoformat())).fetchone()[0]
        if pending >= 2 or recent >= 10: raise HTTPException(429, 'Investigation limit reached; try again later')
        c.execute('''INSERT INTO investigations(id,owner,scope,prompt,status,created_utc,parent_id,root_id,turn_index)
            VALUES(?,?,?,?,?,?,?,?,?)''', (str(body.request_id), body.scope.owner(),body.scope.model_dump_json(),
            body.prompt, 'queued', datetime.now(timezone.utc).isoformat(),parent_id,root_id,turn_index))
    app.state.queue.put_nowait(body)
    return {'id': str(body.request_id), 'status': 'queued', 'root_id':root_id, 'mode': MODE}


@app.post('/history')
def history(scope: Scope, authorization: str = Header(default='')):
    authorize(authorization)
    with connection() as c:
        rows = c.execute('''WITH recent AS (
            SELECT id,owner,root_id,status,created_utc,ROW_NUMBER() OVER(PARTITION BY root_id ORDER BY turn_index DESC) AS rn,
                COUNT(*) OVER(PARTITION BY root_id) AS turn_count
            FROM investigations WHERE owner=?)
            SELECT r.root_id AS id,r.id AS latest_id,initial.prompt,r.status,r.created_utc,r.turn_count
            FROM recent r JOIN investigations initial ON initial.id=r.root_id AND initial.owner=r.owner
            WHERE r.rn=1 ORDER BY r.created_utc DESC LIMIT 50''',(scope.owner(),)).fetchall()
    return {'items': [dict(r) for r in rows], 'mode': MODE}


@app.post('/investigations/{request_id}')
def detail(request_id: uuid.UUID, scope: Scope, authorization: str = Header(default='')):
    authorize(authorization)
    with connection() as c:
        row = c.execute('SELECT * FROM investigations WHERE id=? AND owner=?', (str(request_id), scope.owner())).fetchone()
        if not row: raise HTTPException(404, 'Request not found')
        turns=thread(c,scope.owner(),row['root_id'])
    return public_turn(row,full=True) | {'conversation':[public_turn(r,full=r['id']==row['id']) for r in turns],
                                       'max_turns':MAX_TURNS,'mode':MODE}


# Static synthetic demo only: it never receives the broker token or customer evidence.
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
WEB=ROOT/'web'
if WEB.is_dir():
    app.mount('/assets',StaticFiles(directory=WEB),name='assets')
    @app.get('/')
    @app.get('/demo')
    def demo():
        return FileResponse(WEB/'index.html',headers={
            'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
