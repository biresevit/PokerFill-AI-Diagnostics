import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from backoffice.diagnostics import prepare_logs


def test_collection_disabled_and_viewer_never_write(monkeypatch):
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_COLLECT_LOGS','0')
    assert not prepare_logs(SimpleNamespace(),1006)['enabled']
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_COLLECT_LOGS','1')
    assert not prepare_logs(SimpleNamespace(ctx=SimpleNamespace(role='viewer')),1006)['enabled']


def test_collection_scoped_cached_and_fixed_arguments(monkeypatch):
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_COLLECT_LOGS','1')
    class Db:
        def rows(self,sql,*args):
            assert args==(3,1006)
            assert 'm.IsActive=1' in sql and 'a.SiteId=%s' in sql
            return [{'MachineId':5,'CollectionSiteId':1006},{'MachineId':1030,'CollectionSiteId':1006}]
        def one(self,sql,*args):
            assert args[:2]==(3,1006)
            return {'Status':'ready'} if args[2]==5 else None
    store=SimpleNamespace(db=Db(),ctx=SimpleNamespace(role='operator',customer_id=3))
    with patch('backoffice.machine_logs.MachineLogs') as logs:
        result=prepare_logs(store,1006)
        logs.return_value.create.assert_called_once_with(1006,1030,{'hours':168})
    assert result['machines']==[{'machine_id':5,'status':'ready'},{'machine_id':1030,'status':'requested'}]


def test_shared_pc_queues_under_enrolled_site(monkeypatch):
    monkeypatch.setenv('POKERFILL_DIAGNOSTICS_COLLECT_LOGS','1')
    class Db:
        def rows(self,sql,*args):
            assert args==(14004,1004)
            return [{'MachineId':1,'CollectionSiteId':1}]
        def one(self,sql,*args):
            assert args==(14004,1,1)
            return None
    store=SimpleNamespace(db=Db(),ctx=SimpleNamespace(role='operator',customer_id=14004))
    with patch('backoffice.machine_logs.MachineLogs') as logs:
        prepare_logs(store,1004)
        logs.return_value.create.assert_called_once_with(1,1,{'hours':168})
