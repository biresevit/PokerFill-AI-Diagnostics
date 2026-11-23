import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path
import pytest
pytest.importorskip('langgraph')
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pokerfill_ai import service
from pokerfill_ai.conversations import context,MAX_TURNS


def scope(**kwargs):return dict(customer_id=3,site_id=1006,provider='aad',subject='alice')|kwargs


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'DB_PATH',tmp_path/'history.db')
    monkeypatch.setattr(service,'TOKEN','private-test-token-'*3)
    monkeypatch.setattr(service,'MODE','recorded')
    with TestClient(service.app) as c:
        c.headers['Authorization']='Bearer '+service.TOKEN
        yield c


def ask(client,prompt,parent=None):
    rid=str(uuid.uuid4())
    body=dict(scope=scope(),prompt=prompt,request_id=rid,parent_id=parent)
    assert client.post('/investigations',json=body).status_code==202
    for _ in range(100):
        result=client.post('/investigations/'+rid,json=scope()).json()
        if result['status'] in ('completed','failed'):break
        time.sleep(.02)
    assert result['status']=='completed',result
    return result,body


def test_followup_includes_answer_and_stays_in_one_private_thread(client):
    root,_=ask(client,'Why are fewer ring entries running?')
    reply,body=ask(client,'Why did you call that probable?',root['id'])
    assert reply['parent_id']==root['id'] and reply['root_id']==root['id']
    assert [t['id'] for t in reply['conversation']]==[root['id'],reply['id']]
    assert reply['result']['history_context']['request_ids']==[root['id']]
    evidence=next(e for e in reply['result']['evidence'] if e['source']=='get_investigation_history')
    assert evidence['data']['turns'][0]['question']==root['prompt']
    assert evidence['data']['turns'][0]['answer']['summary']==root['result']['report']['summary']
    history=client.post('/history',json=scope()).json()['items']
    assert len(history)==1 and history[0]['turn_count']==2 and history[0]['latest_id']==reply['id']
    assert client.post('/investigations',json=body).status_code==202
    stale=dict(scope=scope(),prompt='Another branch?',request_id=str(uuid.uuid4()),parent_id=root['id'])
    assert client.post('/investigations',json=stale).status_code==409
    for foreign in (scope(subject='bob'),scope(customer_id=14004),scope(site_id=1),scope(provider='local')):
        assert client.post('/investigations',json=body|dict(scope=foreign,request_id=str(uuid.uuid4()))).status_code==404
        assert client.post('/investigations/'+reply['id'],json=foreign).status_code==404
    other,_=ask(client,'A new independent investigation')
    assert not other['parent_id'] and other['result']['history_context']['included_turns']==0
    assert len(client.post('/history',json=scope()).json()['items'])==2


def test_question_limit_and_pending_parent(client):
    root,_=ask(client,'Initial question')
    owner=service.Scope(**scope()).owner()
    parent=root['id']
    with service.connection() as c:
        for i in range(1,MAX_TURNS):
            rid=str(uuid.uuid4())
            c.execute('''INSERT INTO investigations(id,owner,scope,prompt,status,created_utc,parent_id,root_id,turn_index)
                VALUES(?,?,?,?,?,?,?,?,?)''',(rid,owner,'{}','question','completed',root['created_utc'],parent,root['id'],i))
            parent=rid
    body=dict(scope=scope(),prompt='One too many',request_id=str(uuid.uuid4()),parent_id=parent)
    assert client.post('/investigations',json=body).status_code==409
    with service.connection() as c:c.execute("UPDATE investigations SET status='running' WHERE id=?",(parent,))
    assert client.post('/investigations',json=body).status_code==409


def test_existing_history_migrates_without_losing_answers(tmp_path,monkeypatch):
    path=tmp_path/'legacy.db';monkeypatch.setattr(service,'DB_PATH',path)
    now=service.datetime.now(service.timezone.utc).isoformat()
    saved=json.dumps({'report':{'summary':'Original answer'}})
    with sqlite3.connect(path) as c:
        c.execute('''CREATE TABLE investigations(id TEXT PRIMARY KEY,owner TEXT,scope TEXT,prompt TEXT,status TEXT,created_utc TEXT,result TEXT,error TEXT)''')
        c.execute('INSERT INTO investigations VALUES(?,?,?,?,?,?,?,NULL)',('legacy','alice','{}','Old question','completed',now,saved))
    service.init();service.init()
    with service.connection() as c:
        row=c.execute("SELECT * FROM investigations WHERE id='legacy'").fetchone()
        assert row['result']==saved and row['root_id']=='legacy' and row['turn_index']==0 and row['parent_id'] is None


def test_retention_keeps_old_root_of_recent_conversation(tmp_path,monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(service,'DB_PATH',tmp_path/'retention.db');service.init()
    now=service.datetime.now(service.timezone.utc)
    old=(now-timedelta(days=120)).isoformat()
    with service.connection() as c:
        for rid,root,parent,index,created in [('kept','kept',None,0,old),('reply','kept','kept',1,now.isoformat()),('expired','expired',None,0,old)]:
            c.execute('''INSERT INTO investigations(id,owner,scope,prompt,status,created_utc,root_id,parent_id,turn_index)
                VALUES(?,?,?,?,?,?,?,?,?)''',(rid,'alice','{}','question','completed',created,root,parent,index))
    service.init()
    with service.connection() as c:assert {r[0] for r in c.execute('SELECT id FROM investigations')}=={'kept','reply'}


def test_provider_quota_failure_is_clear_and_keeps_previous_answer(client,monkeypatch):
    root,_=ask(client,'Original operational question')
    class QuotaError(Exception):
        status_code=429
        body={'code':'credit_balance_exhausted','type':'insufficient_quota','message':'PRIVATE-KEY-MARKER'}
    def fail(*args,**kwargs):
        try:
            raise QuotaError()
        except QuotaError as error:
            raise RuntimeError('Model invocation failed') from error
    with monkeypatch.context() as patch:
        patch.setattr(service,'investigate',fail)
        rid=str(uuid.uuid4())
        body=dict(scope=scope(),prompt='Explain your previous answer',request_id=rid,parent_id=root['id'])
        assert client.post('/investigations',json=body).status_code==202
        for _ in range(100):
            failed=client.post('/investigations/'+rid,json=scope()).json()
            if failed['status']=='failed':break
            time.sleep(.02)
        assert failed['status']=='failed' and 'credits are exhausted' in failed['error']
        assert 'PRIVATE-KEY-MARKER' not in json.dumps(failed)
        assert failed['conversation'][0]['result']['report']==root['result']['report']
    retry,_=ask(client,'Please try the previous question again',rid)
    assert len(retry['conversation'])==3
    context_evidence=next(e for e in retry['result']['evidence'] if e['source']=='get_investigation_history')
    assert context_evidence['data']['turns'][-1]['status']=='failed'


def test_history_context_is_bounded_and_keeps_recent_reference():
    rows=[dict(id=str(i),created_utc='2026-11-20',prompt='Question '+str(i),status='completed',
               result=json.dumps({'report':{'summary':'S'*2000,'findings':[{'title':'Cause','explanation':'E'*1500,'certainty':'probable'}]*6,'limitations':['L'*800]*6},'evidence':[]})) for i in range(20)]
    carried=context(rows)
    assert carried[-1]['question']=='Question 19'
    assert carried[0]['question']=='Question 0'
    assert len(carried)<=4 and len(json.dumps(carried))<=16000


def test_long_unicode_context_stays_bounded_and_preserves_anchors():
    rows=[dict(id=str(i),created_utc='2026-11-20',prompt='問題'*1000,status='completed',
        result=json.dumps({'report':{'summary':'説明'*2000,'findings':[{'title':'原因'*500,'explanation':'証拠'*1500}]*6,'limitations':['制限'*800]*6},'evidence':[]})) for i in range(20)]
    carried=context(rows)
    assert carried[0]['request_id']=='0' and carried[-1]['request_id']=='19'
    assert len(json.dumps(carried))<=16000


def test_followup_model_receives_prior_diagnosis_and_can_refresh_evidence():
    from pokerfill_ai.agent import investigate,Report,Finding
    from langchain_core.messages import AIMessage
    class Model:
        calls=0
        def bind_tools(self,tools):
            assert any(t.name=='get_investigation_history' for t in tools)
            return self
        def invoke(self,messages):
            text=' '.join(str(m.content) for m in messages)
            assert 'MTT Alpine' in text and 'get_investigation_history' in text
            self.calls+=1
            if self.calls==1:return AIMessage(content='',tool_calls=[dict(name='get_schedule_compliance',args={},id='fresh')])
            return AIMessage(content='Refreshed schedule')
        def with_structured_output(self,*args,**kwargs):
            class Final:
                def invoke(self,messages):
                    assert 'MTT Alpine' in messages[-1].content
                    return {'raw':AIMessage(content=''),'parsed':Report(summary='Refreshed schedule evidence',findings=[Finding(title='Current schedule',explanation='Use the refreshed schedule rather than the old answer',certainty='confirmed',evidence_ids=['E2'])],limitations=[])}
            return Final()
    carried=[{'request_id':'prior','question':'Why no registrations for MTT Alpine?',
              'answer':{'summary':'The lobby was stale'},'created_utc':'2026-11-19T12:00:00Z'}]
    result=investigate('Is that still true?',service.RecordedData(service.Scope(**scope())),model=Model(),history=carried,history_total=5)
    assert [e['source'] for e in result['evidence']]==['get_investigation_history','get_schedule_compliance']
    assert result['history_context']['included_turns']==1 and result['history_context']['previous_turns']==5


def test_broker_forwards_parent_without_accepting_forged_scope(monkeypatch):
    from backoffice.diagnostics import dispatch
    from backoffice.store import Context
    import io
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_URL','http://127.0.0.1:4310')
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_TOKEN','x'*40)
    parent=str(uuid.uuid4())
    class Store:
        ctx=Context(3,'aad','alice','viewer')
        def site(self,sid):assert sid==1006
    def open_(request,**kwargs):
        payload=json.loads(request.data)
        assert payload['scope']==scope() and payload['parent_id']==parent
        return io.BytesIO(b'{"id":"job"}')
    monkeypatch.setattr('urllib.request.urlopen',open_)
    dispatch(Store(),1006,'POST',None,dict(prompt='Explain that diagnosis',request_id=str(uuid.uuid4()),parent_id=parent,scope=scope(customer_id=999)))


def test_time_budget_and_throttling_have_distinct_messages():
    class RateLimitError(Exception):
        status_code=429

    assert 'four-minute processing limit' in service.failure_message(RuntimeError('Investigation time budget reached'))
    assert 'rate limit' in service.failure_message(RateLimitError())


def test_blocked_diagnosis_leaves_running_state(client, monkeypatch):
    from threading import Event

    unblock = Event()
    monkeypatch.setattr(service, 'JOB_TIMEOUT_SECONDS', .05)
    monkeypatch.setattr(service, 'investigate', lambda *args, **kwargs: unblock.wait(2))
    rid = str(uuid.uuid4())
    try:
        body = dict(scope=scope(), prompt='Investigate the blocked request', request_id=rid)
        assert client.post('/investigations', json=body).status_code == 202
        for _ in range(100):
            row = client.post('/investigations/'+rid, json=scope()).json()
            if row['status'] == 'failed':
                break
            time.sleep(.02)
        assert row['status'] == 'failed'
        assert 'diagnosis service timed out' in row['error']
    finally:
        unblock.set()


def test_incident_window_persists_with_buffer_and_reaches_agent(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    captured = []
    original = service.investigate
    def capture(*args, **kwargs):
        captured.append(kwargs['window'])
        return original(*args, **kwargs)

    monkeypatch.setattr(service, 'investigate', capture)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(hours=1)
    rid = str(uuid.uuid4())
    body = dict(scope=scope(), prompt='Investigate the selected hour', request_id=rid,
                window=dict(mode='incident', from_utc=start.isoformat(), to_utc=end.isoformat()))
    assert client.post('/investigations', json=body).status_code == 202
    for _ in range(100):
        row = client.post('/investigations/'+rid, json=scope()).json()
        if row['status'] in ('completed', 'failed'):
            break
        time.sleep(.02)
    assert row['status'] == 'completed'
    assert captured == [row['window']]
    assert datetime.fromisoformat(row['window']['evidence_from_utc']) == start - timedelta(minutes=15)
    assert datetime.fromisoformat(row['window']['evidence_to_utc']) == end + timedelta(minutes=15)


def test_historical_ring_target_changes_at_boundary_and_edit():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from pokerfill_ai.integrations.pokerfill.data import historical_ring_target

    def at(hour, minute, second=0):
        return datetime(2026, 11, 22, hour, minute, second, tzinfo=timezone.utc)

    def schedule(late_target):
        return {"timezone": "Europe/Rome", "enabled": True, "windows": [
            {"weekday": 6, "start_minute": 1110, "end_minute": 1200, "target": 70},
            {"weekday": 6, "start_minute": 1200, "end_minute": 1440, "target": late_target},
        ]}

    store = SimpleNamespace(historical_schedule=lambda *args: [(at(17, 0), schedule(30)), (at(19, 30, 18), schedule(100))])
    result = historical_ring_target(None, store, None, 1006, at(18, 45), at(20, 15))
    assert [(r["at_utc"], r["target"], r["cause"]) for r in result["changes"]] == [
        (at(18, 45).isoformat(), 70, "initial"),
        (at(19, 0).isoformat(), 30, "weekly_schedule_boundary"),
        (at(19, 30, 18).isoformat(), 100, "schedule_edit"),
    ]
