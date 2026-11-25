import json
import sys
import time
import uuid
from pathlib import Path

import pytest
pytest.importorskip('langgraph', reason='Install diagnostics/requirements.txt in the diagnostics environment')
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokerfill_ai import service
from pokerfill_ai.agent import investigate
from pokerfill_ai.data import Data, TOOLS
from backoffice.validation import Error


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'TOKEN', 'test-private-token-'*3)
    monkeypatch.setattr(service, 'MODE', 'recorded')
    monkeypatch.setattr(service, 'DB_PATH', tmp_path/'history.db')
    with TestClient(service.app) as c:
        c.headers['Authorization']='Bearer '+service.TOKEN
        yield c


def scope(**changes):
    return dict(customer_id=3, site_id=1006, provider='aad', subject='alice') | changes


def test_history_isolation_and_idempotency(client):
    rid=str(uuid.uuid4())
    body=dict(scope=scope(), prompt='Why fewer ring entries?', request_id=rid)
    assert client.post('/investigations',json=body).status_code==202
    assert client.post('/investigations',json=body).json()['id']==rid
    for _ in range(100):
        result=client.post('/investigations/'+rid,json=scope()).json()
        if result['status'] in ('completed','failed'):break
        time.sleep(.02)
    assert result['status']=='completed',result
    assert result['result']['mode']=='recorded'
    assert result['result']['report']['findings'][0]['evidence_ids']==['E2','E3']
    assert len(client.post('/history',json=scope()).json()['items'])==1
    for other in (scope(subject='bob'), scope(customer_id=14004), scope(site_id=1), scope(provider='local')):
        assert client.post('/history',json=other).json()['items']==[]
        assert client.post('/investigations/'+rid,json=other).status_code==404
    assert client.post('/investigations',json=body|{'prompt':'Different prompt'}).status_code==409
    assert client.post('/history',json=scope(),headers={'Authorization':'Bearer wrong'}).status_code==401


def test_validation_and_restart(client):
    assert client.post('/investigations',json=dict(scope=scope(),prompt='x',request_id=str(uuid.uuid4()))).status_code==422
    assert client.post('/history',json=scope(secret='not allowed')).status_code==422
    with service.connection() as c:
        c.execute('INSERT INTO investigations(id,owner,scope,prompt,status,created_utc) VALUES(?,?,?,?,?,?)',('test','owner','{}','question','running',service.datetime.now(service.timezone.utc).isoformat()))
    service.init()
    with service.connection() as c:
        assert c.execute("SELECT status FROM investigations WHERE id='test'").fetchone()[0]=='failed'


def test_data_scope_checked_before_tool():
    from contextlib import contextmanager
    class Db:
        def one(self, sql, *args):
            assert 'SiteId=%s AND CustomerId=%s' in sql
            assert args==(1006,3)
            return None
        def rows(self,*args): raise AssertionError('Out-of-scope query executed')
    @contextmanager
    def factory():yield Db()
    with pytest.raises(Error):Data(service.Scope(**scope()),factory).read('get_join_outcomes')


def test_sql_adapter_rejects_writes():
    from pokerfill_ai.data import DiagnosticDatabase
    db=DiagnosticDatabase(None)
    for sql in ('DELETE FROM scheduler.Entry','SELECT 1; DROP TABLE scheduler.Entry','EXEC scheduler.SetSchedule'):
        with pytest.raises(RuntimeError):db.rows(sql)
    with pytest.raises(RuntimeError):db.execute('SELECT 1')


@pytest.mark.parametrize('name',list(TOOLS))
def test_only_selects_and_fixed_scope(name):
    from contextlib import contextmanager
    queries=[]
    class Db:
        def one(self,sql,*args):
            queries.append((sql,args))
            return dict(id=1006,customer_id=3,platform='bluffpoker',timezone='UTC',enabled=True,version=1,joins_paused=False,currency='USD',n=0)
        def rows(self,sql,*args):
            queries.append((sql,args));return []
    @contextmanager
    def factory():yield Db()
    # Overview has a broader existing helper; tenant gate itself is tested above.
    if name=='get_schedule_compliance':return
    Data(service.Scope(**scope()),factory).read(name,24,"x'; DROP TABLE nope;--")
    for sql,args in queries:
        assert sql.strip().upper().startswith('SELECT')
        assert "DROP TABLE" not in sql
        assert 1006 in args or 'OBJECT_ID(' in sql or ('FROM dbo.Customer WHERE CustomerId=%s' in sql and args==(3,))
        assert 'LoginPassword AS' not in sql and 'PayloadJson' not in sql


def test_graph_live_tool_call_and_structured_evidence():
    from langchain_core.messages import AIMessage
    from pokerfill_ai.agent import Report,Finding
    class Model:
        calls=0
        def bind_tools(self,tools):return self
        def invoke(self,messages):
            self.calls+=1
            if self.calls==1:return AIMessage(content='',tool_calls=[dict(name='get_funding_capacity',args={},id='call1')])
            return AIMessage(content='Wallets unknown')
        def with_structured_output(self,*args,**kwargs):
            class Final:
                def invoke(self,messages):
                    assert 'Evidence provenance:' in messages[0].content
                    assert 'final report formatting phase' in messages[0].content
                    return {'raw':AIMessage(content=''), 'parsed':Report(summary='Funds unknown', findings=[Finding(title='No balance evidence',explanation='No trusted snapshots',certainty='unknown',evidence_ids=['E4'])],limitations=['Funds cannot be confirmed'])}
            return Final()
    result=investigate('Why fewer entries?',service.RecordedData(service.Scope(**scope())),model=Model())
    assert len(result['evidence'])==4
    assert result['evidence'][-1]['source']=='get_funding_capacity'
    assert result['mode']=='live'


def test_broker_derives_scope(monkeypatch):
    from backoffice.diagnostics import dispatch
    from backoffice.store import Context
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_URL','http://127.0.0.1:4310')
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_TOKEN','x'*40)
    class Store:
        ctx=Context(3,'aad','alice','viewer')
        def site(self,sid):assert sid==1006
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return b'{"items":[]}'
    def open_(request,**kwargs):
        assert json.loads(request.data)==scope()
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    dispatch(Store(),1006,'GET',None,{'scope':scope(customer_id=999)})


def test_join_outcomes_expose_platform_code_without_account_names():
    from contextlib import contextmanager
    from datetime import datetime
    from pokerfill_ai.integrations.pokerfill.data import platform_code
    assert platform_code('during joining the table and taking a seat: BluffPokerError: buy-in-seat: MAINTENANCE_SCHEDULED')=='MAINTENANCE_SCHEDULED'
    assert platform_code('BluffPokerError: table: NOT_AT_TABLE')=='NOT_AT_TABLE'
    assert platform_code('Leave failed for Perry')is None and platform_code(None) is None
    at=datetime(2026,11,25,1,30)
    rows=[dict(Kind='leave',Status='failed',ResultCode='BluffPokerError',PlatformTail='g the table: BluffPokerError: table: NOT_AT_TABLE',Count=20,FirstUtc=at,LastUtc=at),
          dict(Kind='leave',Status='failed',ResultCode='BluffPokerError',PlatformTail='he table: BluffPokerError: table: NOT_AT_TABLE',Count=18,FirstUtc=at,LastUtc=at),
          dict(Kind='join',Status='failed',ResultCode='BluffPokerError',PlatformTail='seat: BluffPokerError: buy-in-seat: MAINTENANCE_SCHEDULED',Count=1,FirstUtc=at,LastUtc=at)]
    class Db:
        def one(self,sql,*args):
            return dict(id=1006,customer_id=3,platform='bluffpoker',timezone='UTC',enabled=True,version=1,joins_paused=False,currency='USD',n=0)
        def rows(self,sql,*args):return rows
    @contextmanager
    def factory():yield Db()
    data=Data(service.Scope(**scope()),factory).read('get_join_outcomes',24)['data']
    assert [(g['PlatformCode'],g['Count']) for g in data]==[('NOT_AT_TABLE',38),('MAINTENANCE_SCHEDULED',1)]
    assert all('PlatformTail' not in g for g in data)
