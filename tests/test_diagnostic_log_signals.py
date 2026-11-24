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



def test_parse_deadline_reports_incomplete_coverage(monkeypatch):
    from pokerfill_ai.integrations.pokerfill import log_signals

    times = iter([0, log_signals.PARSE_SECONDS + 1])
    monkeypatch.setattr(log_signals, 'monotonic', lambda: next(times))
    result = summarize(archive(), 1006, 5)

    assert result['scan_time_limit_reached']
    assert result['coverage_truncated']
    assert result['file_coverage'][0]['scan_incomplete']
    assert result['file_coverage'][0]['bytes_scanned'] == 0


def test_explicit_log_window_excludes_known_outside_timestamps():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('manifest.json', json.dumps(dict(site_id=1006, machine_id=5, iana_time_zone='Europe/Budapest')))
        z.writestr('controller/run/worker.log',
                   '2026-11-22 19:59:59 INFO CREDENTIAL_EXPIRED\n'
                   '2026-11-22 20:00:00 INFO CREDENTIAL_EXPIRED\n'
                   '2026-11-22 21:00:00 INFO CREDENTIAL_EXPIRED\n')
    result = summarize(out.getvalue(), 1006, 5, from_utc='2026-11-22T19:00:00Z', to_utc='2026-11-22T20:00:00Z')
    assert result['signals']['CREDENTIAL_EXPIRED'] == 1
    assert result['events'][0]['timestamp_utc'] == '2026-11-22T19:00:00+00:00'


def test_network_failure_watchdog_and_later_epoch_loss_remain_distinguishable():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5,iana_time_zone='Europe/Berlin')))
        z.writestr('controller/run/worker.log',
            '2026-11-23 20:07:54,000 ERROR [site 1006] socket failed\n'
            'OSError: [WinError 64] private-account password=hidden\n'
            '2026-11-23 20:07:55,000 ERROR [site 999] [WinError 10055] secret-other-site\n'
            '2026-11-23 20:09:48,811 ERROR [site 1006] No heartbeat reached the database for 125 s; stopping\n'+
            ''.join(f'2026-11-23 20:11:{i%60:02},000 ERROR [site 1006] Chat synchronization failed\n'
                    'RuntimeError: Chat worker epoch lost secret-account\n' for i in range(100)))
    result=summarize(out.getvalue(),1006,5,around_utc='2026-11-23T19:08:43Z',topic='restart',
                     from_utc='2026-11-23T19:00:00Z',to_utc='2026-11-23T19:30:00Z')
    groups={g['code']:g for g in result['event_groups']}
    assert groups['network_name_unavailable']['first_utc']=='2026-11-23T19:07:54+00:00'
    assert groups['heartbeat_timeout']['first_details']['seconds_without_heartbeat']==125
    assert groups['chat_worker_epoch_lost']['count']==100
    assert groups['chat_worker_epoch_lost']['first_utc']=='2026-11-23T19:11:00+00:00'
    assert groups['chat_worker_epoch_lost']['last_utc']=='2026-11-23T19:11:59+00:00'
    assert result['events_truncated']
    assert 'socket_resources_exhausted' not in groups
    for secret in ('private-account','password=','hidden','secret-account','secret-other-site'):
        assert secret not in json.dumps(result)


def test_connectivity_recognizes_network_error_and_preserves_explicit_utc():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('controller/run/worker.log',
                   '2026-11-23T19:07:54Z ERROR [site 1006] [WinError 64] private\n')
    result=summarize(out.getvalue(),1006,5,topic='connectivity')
    event=result['events'][0]
    assert event['code']=='network_name_unavailable'
    assert event['timestamp_utc']=='2026-11-23T19:07:54+00:00'
    assert event['clock']=='offset_in_timestamp'


def test_worker_progress_exposes_only_fixed_phase_and_await_names():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(dict(site_id=1006,machine_id=5)))
        z.writestr('controller/run/worker.log',
            '2026-11-23T19:09:48Z ERROR [site 1006] Worker diagnostic event=heartbeat_stall '
            'phase=publish phase_age_seconds=121.500 heartbeat_age_seconds=124.000 '
            'event_loop_lag_seconds=0.015 awaiting=run_cloud_worker,publish,to_thread,private_account password=do-not-leak-this\n'
            '2026-11-23T19:09:49Z INFO [site 1006] Worker diagnostic event=phase_started phase=cleanup_live_views\n'
            '2026-11-23T19:09:50Z INFO [site 1006] Worker diagnostic event=phase_finished '
            'phase=cleanup_live_views elapsed_seconds=1.000 outcome=ok\n')
    result=summarize(out.getvalue(),1006,5,topic='restart')
    events={e['code']:e for e in result['events']}
    stall=events['worker_heartbeat_stall']
    assert stall['phase']=='publish'
    assert stall['phase_age_seconds']==121.5 and stall['heartbeat_age_seconds']==124
    assert stall['event_loop_lag_seconds']==.015
    assert stall['awaiting']==['run_cloud_worker','publish','to_thread']
    assert events['worker_phase_started']['phase']=='cleanup_live_views'
    assert events['worker_phase_finished']['outcome']=='ok'
    assert next(g for g in result['event_groups'] if g['code']=='worker_phase_started')['phase']=='cleanup_live_views'
    assert 'do-not-leak-this' not in json.dumps(result) and 'private_account' not in json.dumps(result)
