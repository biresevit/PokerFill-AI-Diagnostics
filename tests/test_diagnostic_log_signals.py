import io
import json
import sys
import zipfile
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pokerfill_ai.integrations.pokerfill.log_signals import summarize


def archive(site_id=1006):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=site_id,machine_id=5,captured_utc='2026-11-19T12:00:00Z')))
        z.writestr('controller/run/worker.log','CREDENTIAL_EXPIRED password=do-not-expose\nCREDENTIAL_EXPIRED\nTimeoutError')
        z.writestr('pdecision/PDecision.log','CREDENTIAL_EXPIRED secret hand cards')
    return out.getvalue()


def test_no_raw_logs_or_hand_data():
    result=summarize(archive(),1006,5)
    assert result['signals']['CREDENTIAL_EXPIRED']==3
    assert result['signals']['TimeoutError']==1
    assert result['files_scanned']==2
    assert 'do-not-expose' not in json.dumps(result)
    assert 'hand cards' not in json.dumps(result)


def test_archive_scope_required():
    with pytest.raises(ValueError):summarize(archive(1),1006,5)
    with pytest.raises(ValueError):summarize(archive(),1006,99)


def lifecycle_archive():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=1030,captured_utc='2026-11-20T12:00:00Z',
            files=[dict(file='controller/run/worker.log',truncated=True,original_bytes=9000000)])))
        z.writestr('controller/run/worker.log',
            '2026-11-19 20:02:29,010 WARNING [site 1006] Poker site 1006 stopped (WorkerFenced: No heartbeat reached the database for 60 s password=hidden); starting it again in 5 s\n'
            '2026-11-19 20:02:30,000 ERROR [site 999] LOGIN_FAILED secret-other-customer\n')
        z.writestr('updater/updater.log','2026-11-19T19:02:00Z Restarting a stopped managed application. token=hidden\n'
            '2026-11-18T19:02:00Z Update failed: hidden\n')
        z.writestr('pdecision/PDecision.log.1','2026-11-19T19:02:01Z ConnectionError hand cards secret\n')
        z.writestr('config/password.log','2026-11-19T19:02:00Z AUTH_FAILED password=hidden\n')
    return out.getvalue()


def test_lifecycle_timestamps_coverage_and_sensitive_content():
    result=summarize(lifecycle_archive(),1006,1030,around_utc='2026-11-19T19:02:00Z',topic='restart')
    codes={x['code'] for x in result['events']}
    assert {'worker_site_restart','heartbeat_timeout','WorkerFenced','supervisor_restart'}<=codes
    assert 'update_failed' not in codes
    assert result['file_coverage'][0]['truncated'] is True
    local=next(x for x in result['events'] if x['code']=='heartbeat_timeout')
    assert local['clock']=='PC local time; timezone unverified'
    assert local['timestamp_reported']=='2026-11-19 20:02:29,010'
    assert result['files_scanned']==3
    encoded=json.dumps(result)
    for value in ('hidden','secret-other-customer','hand cards','password='):
        assert value not in encoded
    assert 'LOGIN_FAILED' not in result['signals']


def test_log_topics_and_validation():
    result=summarize(lifecycle_archive(),1006,1030,topic='connectivity')
    assert [x['code'] for x in result['events']]==['ConnectionError']
    with pytest.raises(ValueError):summarize(lifecycle_archive(),1006,1030,around_utc='2026-11-19T19:02:00')
    with pytest.raises(ValueError):summarize(lifecycle_archive(),1006,1030,topic='anything')


def test_event_count_does_not_imply_full_coverage():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('updater/updater.log','2026-11-19T19:02:00Z Updater restarted directly.\n'*40)
    result=summarize(out.getvalue(),1006,5)
    assert result['matched_events']==40 and len(result['events'])==24 and result['events_truncated']


def test_new_collector_complete_file_and_clock():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5,iana_time_zone='Europe/Budapest')))
        z.writestr('controller/run/worker.log',
            '2026-11-19 20:02:00,000 WARNING [site 1006] No heartbeat reached the database for 60 s\n'+
            ('ordinary log line\n'*550000))
    result=summarize(out.getvalue(),1006,5,around_utc='2026-11-19T19:02:00Z',topic='restart')
    assert result['files_scanned']==1
    assert result['file_coverage'][0]['bytes_scanned']>8*1024*1024
    assert result['events'][0]['timestamp_utc']=='2026-11-19T19:02:00+00:00'
    assert result['requested_around_utc']=='2026-11-19T19:02:00Z'


def test_continuation_keeps_site_and_timestamp():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('controller/run/worker.log',
            '2026-11-19 20:02:00,000 ERROR [site 999] failed\nRuntimeError: private-other-site\n'
            '2026-11-19 20:02:30,000 ERROR [site 1006] failed\nWorkerFenced: private-message\n')
    result=summarize(out.getvalue(),1006,5,topic='restart')
    assert [e['code'] for e in result['events']]==['WorkerFenced']
    assert result['events'][0]['timestamp_reported']=='2026-11-19 20:02:30,000'
    assert 'private' not in json.dumps(result)


def test_shared_archive_requires_explicit_verified_enrollment_site():
    with pytest.raises(ValueError):summarize(archive(1),1004,5)
    result=summarize(archive(1),1004,5,archive_site_id=1)
    assert result['machine_id']==5
    with pytest.raises(ValueError):summarize(archive(1),1004,5,archive_site_id=1006)


def test_old_file_is_not_scanned_for_focused_incident():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5,files=[
            dict(file='worker/old.log',modified_utc='2026-11-16T00:00:00Z')])))
        z.writestr('worker/old.log','2026-11-16T00:00:00Z Updater restarted directly.\n')
    result=summarize(out.getvalue(),1006,5,around_utc='2026-11-19T19:02:00Z',topic='restart')
    assert result['files_scanned']==0 and not result['events']
    assert result['file_coverage'][0]['scanned'] is False


def test_site_identifiers_in_lifecycle_messages_are_scoped():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('controller/run/worker.log',
            '2026-11-19 20:02:00,000 WARNING Poker site 999 stopped (ended); starting it again in 5 s\n'
            '2026-11-19 20:02:30,000 WARNING Poker site 1006 stopped (ended); starting it again in 5 s\n')
    result=summarize(out.getvalue(),1006,5,topic='restart')
    assert len(result['events'])==1
    assert result['events'][0]['timestamp_reported']=='2026-11-19 20:02:30,000'
    assert result['events'][0]['site_attribution']=='tagged'


def test_closed_table_startup_error_is_exposed_without_raw_details():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('controller/run/worker.log',
            '2026-11-20 14:55:53,650 WARNING Poker site 1006 stopped (connection: TABLE_CLOSED (code=404, ext=None)); starting it again in 20 s password=private\n')
    result=summarize(out.getvalue(),1006,5,topic='restart')
    assert {'worker_site_restart','table_closed_response'}=={e['code'] for e in result['events']}
    assert 'private' not in json.dumps(result)
