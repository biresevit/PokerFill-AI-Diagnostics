import copy
import json

import tiktoken

from pokerfill_ai.model_context import model_evidence


def test_large_worker_evidence_keeps_causes_and_coverage_in_model_budget():
    groups=[{'code':code,'source':'controller','site_attribution':'tagged','count':count,
             'clock':'collector timezone: Europe/Berlin','first_utc':stamp,'last_utc':stamp,
             'first_reported':'2026-11-23 20:09:48,811','last_reported':'2026-11-23 20:09:48,811'}
            for code,count,stamp in [('RuntimeError',295,'2026-11-23T19:11:01Z'),
                ('chat_worker_epoch_lost',295,'2026-11-23T19:11:01Z'),
                ('network_name_unavailable',16,'2026-11-23T19:01:04Z'),
                ('heartbeat_timeout',1,'2026-11-23T19:09:48Z')]]
    groups[-1]['first_details']={'seconds_without_heartbeat':124}
    item={'id':'E5','source':'get_worker_log_signals','data':{'archives':[{
        'machine_id':5,'collector_time_zone':'Europe/Berlin','scan_time_limit_reached':True,
        'event_groups':groups,'events':[{'code':'RuntimeError','timestamp_reported':'2026-11-23 20:11:01'}]*24,
        'file_coverage':[{'source':'controller','scanned':True,'scan_incomplete':True,'truncated':False,
                          'last_timestamp_reported':'2026-11-23 20:22:59'}]*12}]}}
    original=copy.deepcopy(item)
    view=model_evidence(item)
    archive=view['data']['archives'][0]
    assert view['id']=='E5' and view['model_view_compacted']
    by_code={g['code']:g for g in archive['event_groups']}
    assert {'network_name_unavailable','heartbeat_timeout','chat_worker_epoch_lost'}<=by_code.keys()
    assert by_code['heartbeat_timeout']['first_details']['seconds_without_heartbeat']==124
    assert archive['scan_time_limit_reached']
    assert archive['file_coverage'][0]['scan_incomplete']
    assert archive['events_omitted']
    assert item==original
    text=json.dumps(view,ensure_ascii=False,separators=(',',':'))
    assert len(tiktoken.get_encoding('o200k_base').encode(text))<=900


def test_pending_collection_remains_visible_in_compact_view():
    item={'id':'E1','source':'get_worker_log_signals','data':{
        'inventory':[{'machine_id':5,'collections':[{'Status':'collecting'}]}],'archives':[]}}
    assert model_evidence(item)['data']['inventory'][0]['collections'][0]['Status']=='collecting'
