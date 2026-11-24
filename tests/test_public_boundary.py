from fastapi.testclient import TestClient
from pokerfill_ai import service


def test_public_demo_never_embeds_server_authentication(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'MODE','recorded')
    monkeypatch.setattr(service,'TOKEN','private-test-broker-token-'*3)
    monkeypatch.setattr(service,'DB_PATH',tmp_path/'history.sqlite3')
    with TestClient(service.app) as client:
        page=client.get('/demo')
        assert page.status_code==200
        assert service.TOKEN not in page.text
        assert 'Verified incident, anonymized replay' in page.text
        assert "frame-ancestors 'none'" in page.headers['content-security-policy']
        assert client.get('/health').json()['status']=='ready'


def test_private_api_rejects_before_parsing_body(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'MODE','recorded')
    monkeypatch.setattr(service,'TOKEN','private-test-broker-token-'*3)
    monkeypatch.setattr(service,'DB_PATH',tmp_path/'history.sqlite3')
    with TestClient(service.app) as client:
        for path in ('/investigations','/history'):
            result=client.post(path,content='not even JSON',headers={'content-type':'application/json'})
            assert result.status_code==401
