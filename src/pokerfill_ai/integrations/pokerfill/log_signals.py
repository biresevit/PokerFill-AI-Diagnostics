"""Read scoped application archives; return timestamped operational evidence, never raw lines."""
import io
import json
import re
import zipfile
from collections import Counter
from bisect import bisect_right
from time import monotonic
from ...trace import stage
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SIGNALS = ('CREDENTIAL_EXPIRED','LOGIN_REPLACED','INSUFFICIENT_CHIPS','INSUFFICIENT_FUNDS',
           'LOGIN_FAILED','AUTH_FAILED','CONNECTION_CLOSED','TimeoutError','ConnectionError',
           'SQLTransientConnectionError','join_failure_limit')
MAX_BYTES = 64*1024*1024
PARSE_SECONDS = 25


# Match known operational templates, never return exception messages or arbitrary log text.
EVENTS = {
 'worker_site_restart': r'Poker site \d+ stopped \(',
 'table_closed_response': r'\bTABLE_CLOSED\b',
 'platform_maintenance': r'\bMAINTENANCE_SCHEDULED\b',
 'platform_unknown_table': r'\bUNKNOWN_TABLE\b',
 'platform_not_at_table': r'\bNOT_AT_TABLE\b',
 'lobby_observer_timeout': r'Lobby observer failed for \S+: TimeoutError',
 'lobby_scan_skipped': r'Lobby scan skipped this round',
 'worker_started': r'Playing poker site \d+ on machine \d+',
 'heartbeat_timeout': r'No heartbeat reached the database for \d+',
 'database_retry': r'Temporary SQL failure',
 'database_unreachable': r'The database cannot be reached on a new connection',
 'restart_preserving_seats': r'restarting with the seats kept for reconciliation',
 'ownership_lost': r'Leaving every ring table: the scheduler no longer counts',
 'worker_stopped': r'Cloud worker stopped|PokerController worker stopped by operator',
 'supervisor_restart': r'Restarting a stopped managed application',
 'updater_restart': r'Updater restarted',
 'installation_approved': r'Installation approved by the control plane',
 'applications_starting': r'Starting the new applications',
 'update_healthy': r'Update healthy; traffic recovery',
 'update_failed': r'Update failed:',
 'update_rollback': r'Rolled back to the previous application pair',
 'supervision_failed': r'Supervision failed:',
 'database_deadlock': r'(?i)deadlock victim|transaction.*deadlocked',
 'database_throttled': r'(?i)database.*(?:throttl|resource limit)|error (?:40501|10928|10929)',
 'database_login_failed': r'(?i)login failed for user',
 'database_schema_error': r'(?i)invalid (?:object|column) name',
 'database_integrity_error': r'(?i)violation of (?:primary key|unique key)|cannot insert duplicate',
 'connection_refused': r'(?i)connection refused|actively refused|WinError 10061',
 'connection_reset': r'(?i)connection reset|WinError 10054',
 'out_of_memory': r'(?i)out of memory|MemoryError',
 'application_startup_exit': r'exited during startup \(exit code -?\d+\)',
 'network_name_unavailable': r'\bWinError 64\b',
 'socket_resources_exhausted': r'\bWinError 10055\b|(?i:no buffer space available)',
 'thread_creation_failed': r"(?i:can.t start new thread|cannot start new thread)",
 'event_loop_closed': r'Event loop is closed',
 'chat_worker_epoch_lost': r'Chat worker epoch lost',
 'poker_connection_closed': r'websockets\.exceptions\.ConnectionClosedError',
 'worker_ready': r'Cloud worker ready after reconciling \d+ prior entries',
 'worker_heartbeat_stall': r'Worker diagnostic event=heartbeat_stall\b',
 'worker_phase_started': r'Worker diagnostic event=phase_started\b',
 'worker_phase_finished': r'Worker diagnostic event=phase_finished\b',
}
STAMP = re.compile(r'^(?:\[\s*\d+\]\s*)?(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)')
ERROR_TYPES = ('WorkerFenced','TimeoutError','ConnectionError','SQLTransientConnectionError',
               'OperationalError','ProgrammingError','RuntimeError','CancelledError','KeyError','TypeError',
               'ValueError','AttributeError','MemoryError','PermissionError','OSError','RecursionError')
EVENT_MARKERS = (
    'poker site ', 'table_closed', 'no heartbeat', 'temporary sql failure',
    'database cannot be reached', 'restarting with the seats', 'leaving every ring table',
    'cloud worker stopped', 'pokercontroller worker stopped', 'restarting a stopped',
    'updater restarted', 'installation approved', 'starting the new applications',
    'update healthy', 'update failed', 'rolled back', 'supervision failed', 'deadlock',
    'database', '40501', '10928', '10929', 'login failed for user', 'invalid ',
    'violation of ', 'cannot insert duplicate', 'connection refused', 'actively refused',
    '10061', 'connection reset', '10054', 'out of memory', 'memoryerror', 'exited during startup',
    'winerror 64', '10055', 'no buffer space', 'start new thread', 'event loop is closed',
    'chat worker epoch lost', 'connectionclosederror', 'cloud worker ready',
    'worker diagnostic', 'maintenance_scheduled', 'unknown_table', 'not_at_table',
    'lobby observer failed', 'lobby scan skipped',
)
PLATFORM_EVENTS=('table_closed_response','platform_maintenance','platform_unknown_table','platform_not_at_table',
                 'lobby_observer_timeout','lobby_scan_skipped')
LOG_MARKERS = tuple(code.lower() for code in (*SIGNALS, *ERROR_TYPES)) + EVENT_MARKERS
SITE_TAG = re.compile(r'\[site (\d+)\]')
NAMED_SITE = re.compile(r'(?:Poker site|Playing poker site) (\d+) (?:stopped|on machine|was taken off)')
SIGNAL_PATTERNS={c:re.compile(r'(?<![A-Za-z_])'+re.escape(c)+r'(?![A-Za-z_])') for c in (*SIGNALS,*ERROR_TYPES)}
EVENT_PATTERNS={c:re.compile(p) for c,p in EVENTS.items()}
WORKER_PHASES=frozenset(('startup_configuration','startup_decision','startup_reconciliation','refresh','publish',
    'claim','resource_telemetry','decision_metrics','cleanup_watchdog','cleanup_live_views','cleanup_manual',
    'cleanup_chat','cleanup_fenced','cleanup_clients','cleanup_decision','idle'))
AWAIT_NAMES=frozenset(('run_cloud_worker','configuration','require_hand_history','reconcile_cloud_entries',
    'refresh','publish','to_thread','wait_for','wait','gather','acquire','sleep','close','stop_fenced',
    'leave_unreported','run_live_views','run_manual','run_chat','request','send','recv','receive',
    'ensure_login','login','connect','open','table','lobby','snapshot','_wait','_wait_for_data'))


def timestamp(line):
    match=STAMP.match(line)
    return match.group(1).replace('/','-') if match else None


def near(stamp, around, window=None):
    if not stamp or (not around and not window):return True
    try:value=datetime.fromisoformat(stamp.replace(',', '.').replace('Z','+00:00'))
    except ValueError:return False
    # Worker logs use PC-local wall time with no zone. Never silently label them UTC.
    if window:
        start,end=window
        if value.tzinfo is None:
            return start.date()-timedelta(days=1)<=value.date()<=end.date()+timedelta(days=1)
        return start<=value<end
    if value.tzinfo is None:return abs((value.date()-around.date()).days)<=1
    return abs((value-around).total_seconds())<=3600


def summarize(blob, site_id, machine_id, *, around_utc="", topic="all", archive_site_id=None, from_utc=None, to_utc=None):
    around=datetime.fromisoformat(around_utc.replace("Z","+00:00")) if around_utc else None
    window=None
    if from_utc and to_utc:
        window=tuple(datetime.fromisoformat(value.replace('Z','+00:00')) for value in (from_utc,to_utc))
        if any(value.tzinfo is None for value in window) or window[0]>=window[1]:
            raise ValueError('Invalid archive window')
        around=window[0]+(window[1]-window[0])/2
    if around is not None and around.tzinfo is None:raise ValueError("Incident time must include timezone")
    if topic not in ("all","restart","authentication","funding","connectivity"):raise ValueError("Invalid log topic")
    if len(blob)>MAX_BYTES: raise ValueError('Archive too large')
    counts=Counter(); events=[]; ranks=[]; matched=0; coverage=[]; groups={}
    deadline=monotonic()+PARSE_SECONDS
    parse_limited=False
    incident_clock=around.astimezone(timezone.utc).replace(tzinfo=None) if around else None

    def rank(event):
        raw=event.get('timestamp_utc') or event['timestamp_reported']
        try:value=datetime.fromisoformat(raw.replace(',','.').replace('Z','+00:00')) if raw else None
        except ValueError:value=None
        value_clock=value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value
        distance=abs((value_clock-incident_clock).total_seconds()) if incident_clock and value_clock else -value_clock.replace(tzinfo=timezone.utc).timestamp() if value_clock else float('inf')
        return (0 if event['code'] in EVENTS else 1,distance)

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        entries=z.infolist()
        if len(entries)>256 or sum(i.file_size for i in entries)>640*1024*1024:
            raise ValueError('Archive limits exceeded')
        info=z.getinfo('manifest.json')
        if info.file_size>128*1024: raise ValueError('Manifest too large')
        manifest=json.loads(z.read(info))
        if manifest.get('site_id')!=(site_id if archive_site_id is None else archive_site_id) or manifest.get('machine_id')!=machine_id:
            raise ValueError('Archive scope mismatch')
        zone=None
        try:
            name=manifest.get('iana_time_zone','')
            if isinstance(name,str) and len(name)<=80 and re.fullmatch(r'[A-Za-z0-9_+/-]+',name):zone=ZoneInfo(name)
        except (ZoneInfoNotFoundError,ValueError):pass
        files=0
        indexed=list(enumerate(entries))
        if topic in ('restart','connectivity'):
            indexed.sort(key=lambda pair:{'controller':0,'worker':0,'updater':1,'pdecision':2}.get(pair[1].filename.split('/')[0].lower(),3))
        for file_number,i in indexed:
            # Only known application log roots; raw lines/hand data never leave this parser.
            if not i.filename.lower().startswith(('controller/','worker/','updater/','pdecision/')) or not re.search(r'\.log(?:[.\d-].*)?$',i.filename.lower()):
                continue
            if i.file_size>160*1024*1024: continue
            source=i.filename.split('/')[0].lower()
            meta=next((f for f in manifest.get('files',[]) if f.get('file')==i.filename),{})
            modified=timestamp(str(meta.get('modified_utc','')))
            try:modified_time=datetime.fromisoformat(modified.replace('Z','+00:00')) if modified else None
            except ValueError:modified_time=None
            if around and modified_time and modified_time.tzinfo and modified_time<(window[0] if window else around-timedelta(hours=1)):
                coverage.append({'source':source,'file_index':file_number,'scanned':False,
                    'reason':'File modification precedes requested incident window; filesystem UTC metadata, not a complete content audit.',
                    'modified_utc':modified,'bytes_scanned':0})
                continue
            marks=[];record_stamp=None;record_site=None;bytes_scanned=0
            with z.open(i) as raw:
                for line_number, raw_line in enumerate(raw):
                    if line_number%256==0 and monotonic()>=deadline:
                        parse_limited=True
                        break
                    bytes_scanned+=len(raw_line)
                    line=raw_line.decode('utf-8',errors='replace').rstrip('\r\n')
                    stamp=timestamp(line)
                    if stamp:
                        if not marks:marks.append(stamp)
                        elif len(marks)==1:marks.append(stamp)
                        else:marks[-1]=stamp
                    if stamp:
                        record_stamp=stamp;record_site=None
                    tagged=SITE_TAG.search(line) if '[site ' in line else None
                    if tagged:record_site=int(tagged.group(1))
                    lowered=line.lower()
                    named_site=NAMED_SITE.search(line) if 'poker site ' in lowered else None
                    if named_site:record_site=int(named_site.group(1))
                    if not any(marker in lowered for marker in LOG_MARKERS):continue
                    # Skip explicitly tagged lines from another site on shared PCs.
                    if record_site is not None and record_site!=site_id:continue
                    stamp=stamp or record_stamp
                    resolved=stamp
                    if stamp and zone and source in ('controller','worker'):
                        try:
                            local=datetime.fromisoformat(stamp.replace(',','.').replace('Z','+00:00'))
                            # Ambiguous DST wall times remain unverified.
                            if local.tzinfo is None and local.replace(tzinfo=zone,fold=0).utcoffset()==local.replace(tzinfo=zone,fold=1).utcoffset():
                                resolved=local.replace(tzinfo=zone).isoformat()
                        except ValueError:pass
                    if not near(resolved,around,window):continue
                    for code in SIGNALS:
                        counts[code]+=len(SIGNAL_PATTERNS[code].findall(line))
                    codes=[code for code,pattern in EVENT_PATTERNS.items() if pattern.search(line)
                           and (topic in ('all','restart') or topic=='connectivity' and code in
                                ('network_name_unavailable','socket_resources_exhausted','connection_reset',
                                 'connection_refused','poker_connection_closed','database_unreachable','database_retry',
                                 *PLATFORM_EVENTS))]
                    allowed=(*SIGNALS,*ERROR_TYPES) if topic=='all' else {
                        'authentication':('CREDENTIAL_EXPIRED','LOGIN_REPLACED','LOGIN_FAILED','AUTH_FAILED'),
                        'funding':('INSUFFICIENT_CHIPS','INSUFFICIENT_FUNDS'),
                        'connectivity':('CONNECTION_CLOSED','TimeoutError','ConnectionError','SQLTransientConnectionError'),
                        'restart':ERROR_TYPES}.get(topic,())
                    codes += [code for code in allowed if SIGNAL_PATTERNS[code].search(line)]
                    for code in codes:
                        event={'code':code,'timestamp_reported':stamp,'clock':'offset_in_timestamp' if stamp and re.search(r'(Z|[+-]\d{2}:\d{2})$',stamp) else 'PC local time; timezone unverified',
                               'source':source,'file_index':file_number,'site_attribution':'tagged' if record_site else 'machine-wide; site attribution unconfirmed'}
                        if resolved!=stamp:
                            event['timestamp_utc']=datetime.fromisoformat(resolved).astimezone(timezone.utc).isoformat()
                            event['clock']='collector timezone: '+str(zone)
                        elif stamp:
                            try:
                                value=datetime.fromisoformat(stamp.replace(',','.').replace('Z','+00:00'))
                                if value.tzinfo is not None:event['timestamp_utc']=value.astimezone(timezone.utc).isoformat()
                            except ValueError:pass
                        if code=='application_startup_exit':
                            event['exit_code']=int(re.search(r'exit code (-?\d+)',line).group(1))
                        if code=='heartbeat_timeout':
                            event['seconds_without_heartbeat']=int(re.search(r'No heartbeat reached the database for (\d+)',line).group(1))
                        if code in ('worker_heartbeat_stall','worker_phase_started','worker_phase_finished'):
                            phase_match=re.search(r'\bphase=([a-z_]{1,40})\b',line)
                            if phase_match and phase_match.group(1) in WORKER_PHASES:event['phase']=phase_match.group(1)
                            for field in ('phase_age_seconds','heartbeat_age_seconds','event_loop_lag_seconds','elapsed_seconds'):
                                value=re.search(r'\b'+field+r'=(\d{1,8}(?:\.\d{1,3})?)(?:\s|$)',line)
                                if value:event[field]=float(value.group(1))
                            value=re.search(r'\bawaiting=([A-Za-z_,]{1,256})(?:\s|$)',line)
                            if value:event['awaiting']=[name for name in value.group(1).split(',')[:8] if name in AWAIT_NAMES]
                            value=re.search(r'\boutcome=(ok|interrupted)(?:\s|$)',line)
                            if value:event['outcome']=value.group(1)
                        # Keep first/last observations even when repeated exceptions fill the
                        # 24-event detail budget. Never retain arbitrary messages or stack text.
                        key=(code,source,event['site_attribution'],event['clock'],event.get('phase'))
                        group=groups.setdefault(key,{'code':code,'source':source,'site_attribution':event['site_attribution'],
                            'count':0,'first_event':event,'last_event':event})
                        group['count']+=1
                        def ordering(item):return item.get('timestamp_utc') or item.get('timestamp_reported') or ''
                        if ordering(event)<ordering(group['first_event']):group['first_event']=event
                        if ordering(event)>ordering(group['last_event']):group['last_event']=event
                        matched+=1
                        priority=rank(event)
                        position=bisect_right(ranks,priority)
                        if position<24:
                            ranks.insert(position,priority)
                            events.insert(position,event)
                            del ranks[24:]
                            del events[24:]
            coverage.append({'source':source,'file_index':file_number,'scanned':True,'first_timestamp_reported':marks[0] if marks else None,
                'last_timestamp_reported':marks[-1] if marks else None,'bytes_scanned':bytes_scanned,
                'scan_incomplete':parse_limited,
                'truncated':meta.get('truncated') if isinstance(meta.get('truncated'),bool) else 'unknown','original_bytes':meta.get('original_bytes') if isinstance(meta.get('original_bytes'),int) else None})
            files+=1
            if parse_limited:break
    grouped=sorted(groups.values(),key=lambda group:rank(group['first_event']))
    summaries=[]
    for group in grouped[:16]:
        first,last=group['first_event'],group['last_event']
        summary={k:v for k,v in group.items() if k not in ('first_event','last_event')}
        summary.update(clock=first['clock'],first_reported=first['timestamp_reported'],last_reported=last['timestamp_reported'])
        if first.get('timestamp_utc'):summary['first_utc']=first['timestamp_utc']
        if last.get('timestamp_utc'):summary['last_utc']=last['timestamp_utc']
        if first.get('phase'):summary['phase']=first['phase']
        fields=('seconds_without_heartbeat','phase_age_seconds','heartbeat_age_seconds',
                'event_loop_lag_seconds','elapsed_seconds','awaiting','outcome','exit_code')
        for label,event in (('first_details',first),('last_details',last)):
            details={key:event[key] for key in fields if key in event}
            if details:summary[label]=details
        summaries.append(summary)
    return {'machine_id':machine_id,'captured_utc':timestamp(str(manifest.get('captured_utc',''))), 'signals':{k:v for k,v in counts.items() if v}, 'files_scanned':files,
            'events':events,'matched_events':matched,'events_truncated':matched>24,'file_coverage':coverage[:24],
            'event_groups':summaries,'event_groups_truncated':len(grouped)>16,
            'coverage_truncated':len(coverage)>24 or parse_limited,'scan_time_limit_reached':parse_limited,
            'collection_omissions':dict(Counter('budget_exhausted' if f.get('skipped')=='Uncompressed collection budget exhausted' else 'file_unavailable' for f in manifest.get('files',[]) if f.get('skipped'))),
            'topic':topic,'requested_around_utc':None if window else around_utc or None,
            'from_utc':from_utc,'to_utc':to_utc,'collector_time_zone':str(zone) if zone else None,
            'event_ranking':'Lifecycle templates first, then proximity to requested wall-clock time (local timezone unverified) or latest reported time; bounded to 24 events.',
            'limitations':['Counts cover bounded archive tails, not necessarily the whole requested time range.',
                *(['Archive parsing reached its time limit; only the scanned portion is represented.'] if parse_limited else []),
                *(['Unverified local timestamps and undated lines may fall outside the requested interval.'] if window else []),
                'Signals may repeat one incident. A zero count does not establish absence.',
                'Event groups preserve first/last matches in scanned content, not process uptime or proof of continuous failure. Later ownership errors may be consequences of an earlier heartbeat lapse.',
                'Raw lines, account identities, secrets and hand data are never returned.']}


def read(db, scope, hours, *, machine_id=0, around_utc='', topic='all', from_utc=None, to_utc=None):
    from azure.storage.blob import BlobServiceClient
    import os
    connection=os.getenv('BACKOFFICE_DIAGNOSTICS_STORAGE') or os.getenv('StorageConnString')
    account_url=os.getenv('POKERFILL_LOG_STORAGE_URL')
    if not connection and not account_url:return {'available':False,'reason':'Existing log archive storage is not configured'}
    machines=db.rows("""SELECT TOP(24) m.MachineId FROM dbo.CustomerMachine m
        JOIN scheduler.MachineAssignment a ON a.MachineId=m.MachineId
        WHERE m.CustomerId=%s AND a.SiteId=%s AND m.IsActive=1 AND (%s=0 OR m.MachineId=%s)
        ORDER BY m.MachineId""",scope.customer_id,scope.site_id,machine_id,machine_id)
    rows=[]; inventory=[]
    now=datetime.now(timezone.utc)
    since=(datetime.fromisoformat(from_utc) if from_utc else now-timedelta(hours=hours)).astimezone(timezone.utc).replace(tzinfo=None)
    incident_end=datetime.fromisoformat(to_utc).astimezone(timezone.utc).replace(tzinfo=None) if to_utc else None
    for machine in machines:
        mid=machine['MachineId']
        recent=db.rows("""SELECT TOP(4) r.SiteId,r.MachineId,r.BlobName,r.SizeBytes,r.CreatedUtc,r.Status,r.ExpiresUtc
            FROM backoffice.MachineLogRequest r
            WHERE r.CustomerId=%s AND r.MachineId=%s
            AND EXISTS(SELECT 1 FROM scheduler.Site s WHERE s.SiteId=r.SiteId AND s.CustomerId=r.CustomerId)
            AND r.ExpiresUtc>SYSUTCDATETIME() AND r.CreatedUtc>=%s
            ORDER BY CASE WHEN %s IS NOT NULL AND r.CreatedUtc>=%s THEN 0 ELSE 1 END,
                     CASE WHEN %s IS NOT NULL AND r.CreatedUtc>=%s THEN r.CreatedUtc END ASC,
                     r.CreatedUtc DESC""",scope.customer_id,mid,since,incident_end,incident_end,incident_end,incident_end)
        inventory.append({'machine_id':mid,'collections':[{k:v for k,v in r.items() if k not in ('BlobName','SizeBytes','MachineId','ExpiresUtc')} for r in recent[:2]]})
        ready=next((r for r in recent if r['Status']=='ready'),None)
        if ready:rows.append(ready)
    # Bound storage/network work. Narrow by machine to inspect other PCs.
    rows=rows[:1]
    results=[]
    result={'inventory':inventory,'archives':results,'archive_limit':1,'machines_truncated':len(machines)==24,
            'limitations':['Reads completed unexpired archives; pending collections are listed. Recheck after collection completes. At most one machine archive per call; select machine_id for others. No raw text, paths, hand data or account identities are sent to the model. Machine-wide events cannot prove which site activity was affected.']}
    if not rows:
        return result
    if connection:
        storage=BlobServiceClient.from_connection_string(connection,connection_timeout=5,read_timeout=10,retry_total=0)
    else:
        from azure.identity import DefaultAzureCredential
        storage=BlobServiceClient(account_url=account_url,credential=DefaultAzureCredential(),connection_timeout=5,read_timeout=10,retry_total=0)
    with storage as client:
        for row in rows:
            if not row['SizeBytes'] or row['SizeBytes']>MAX_BYTES:continue
            blob=client.get_blob_client('machine-diagnostics',row['BlobName'])
            with stage('worker_log_archive_properties'):
                if blob.get_blob_properties().size>MAX_BYTES:continue
            with stage('worker_log_archive_download'):
                download_deadline=monotonic()+25
                archive=bytearray()
                for chunk in blob.download_blob(max_concurrency=1).chunks():
                    if monotonic()>=download_deadline:
                        raise TimeoutError('Worker log download budget reached')
                    if len(archive)+len(chunk)>MAX_BYTES:
                        raise ValueError('Archive too large')
                    archive.extend(chunk)
            with stage('worker_log_archive_parse'):
                results.append(summarize(archive,scope.site_id,row['MachineId'],around_utc=around_utc,topic=topic,archive_site_id=row['SiteId'],from_utc=from_utc,to_utc=to_utc))
    return result
