"""Read scoped application archives; return timestamped operational evidence, never raw lines."""
import io
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SIGNALS = ('CREDENTIAL_EXPIRED','LOGIN_REPLACED','INSUFFICIENT_CHIPS','INSUFFICIENT_FUNDS',
           'LOGIN_FAILED','AUTH_FAILED','CONNECTION_CLOSED','TimeoutError','ConnectionError',
           'SQLTransientConnectionError','join_failure_limit')
MAX_BYTES = 64*1024*1024


# Match known operational templates, never return exception messages or arbitrary log text.
EVENTS = {
 'worker_site_restart': r'Poker site \d+ stopped \(',
 'table_closed_response': r'\bTABLE_CLOSED\b',
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
}
STAMP = re.compile(r'^(?:\[\s*\d+\]\s*)?(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)')
ERROR_TYPES = ('WorkerFenced','TimeoutError','ConnectionError','SQLTransientConnectionError',
               'OperationalError','ProgrammingError','RuntimeError','CancelledError','KeyError','TypeError',
               'ValueError','AttributeError','MemoryError','PermissionError','OSError','RecursionError')
GATE=re.compile('|'.join('(?:'+p.replace('(?i)','')+')' for p in [*EVENTS.values(),*(re.escape(c) for c in (*SIGNALS,*ERROR_TYPES))]),re.I)
SIGNAL_PATTERNS={c:re.compile(r'(?<![A-Za-z_])'+re.escape(c)+r'(?![A-Za-z_])') for c in (*SIGNALS,*ERROR_TYPES)}
EVENT_PATTERNS={c:re.compile(p) for c,p in EVENTS.items()}


def timestamp(line):
    match=STAMP.match(line)
    return match.group(1).replace('/','-') if match else None


def near(stamp, around):
    if not around or not stamp:return True
    try:value=datetime.fromisoformat(stamp.replace(',', '.').replace('Z','+00:00'))
    except ValueError:return False
    # Worker logs use PC-local wall time with no zone. Never silently label them UTC.
    if value.tzinfo is None:return abs((value.date()-around.date()).days)<=1
    return abs((value-around).total_seconds())<=3600


def summarize(blob, site_id, machine_id, *, around_utc="", topic="all", archive_site_id=None):
    around=datetime.fromisoformat(around_utc.replace("Z","+00:00")) if around_utc else None
    if around is not None and around.tzinfo is None:raise ValueError("Incident time must include timezone")
    if topic not in ("all","restart","authentication","funding","connectivity"):raise ValueError("Invalid log topic")
    if len(blob)>MAX_BYTES: raise ValueError('Archive too large')
    counts=Counter(); events=[]; matched=0; coverage=[]
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
        for file_number,i in enumerate(entries):
            # Only known application log roots; raw lines/hand data never leave this parser.
            if not i.filename.lower().startswith(('controller/','worker/','updater/','pdecision/')) or not re.search(r'\.log(?:[.\d-].*)?$',i.filename.lower()):
                continue
            if i.file_size>160*1024*1024: continue
            source=i.filename.split('/')[0].lower()
            meta=next((f for f in manifest.get('files',[]) if f.get('file')==i.filename),{})
            modified=timestamp(str(meta.get('modified_utc','')))
            try:modified_time=datetime.fromisoformat(modified.replace('Z','+00:00')) if modified else None
            except ValueError:modified_time=None
            if around and modified_time and modified_time.tzinfo and modified_time<around-timedelta(hours=1):
                coverage.append({'source':source,'file_index':file_number,'scanned':False,
                    'reason':'File modification precedes requested incident window; filesystem UTC metadata, not a complete content audit.',
                    'modified_utc':modified,'bytes_scanned':0})
                continue
            text=z.read(i).decode('utf-8',errors='replace')
            stamps=[];record_stamp=None;record_site=None
            for line in text.splitlines():
                stamp=timestamp(line)
                if stamp:
                    if not stamps:stamps.append(stamp)
                    elif len(stamps)==1:stamps.append(stamp)
                    else:stamps[-1]=stamp
                if stamp:
                    record_stamp=stamp;record_site=None
                tagged=re.search(r'\[site (\d+)\]',line)
                if tagged:record_site=int(tagged.group(1))
                named_site=re.search(r'(?:Poker site|Playing poker site) (\d+) (?:stopped|on machine|was taken off)',line)
                if named_site:record_site=int(named_site.group(1))
                if not GATE.search(line):continue
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
                if not near(resolved,around):continue
                for code in SIGNALS:
                    counts[code]+=len(SIGNAL_PATTERNS[code].findall(line))
                codes=[code for code,pattern in EVENT_PATTERNS.items() if pattern.search(line)] if topic in ('all','restart') else []
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
                    if code=='application_startup_exit':
                        event['exit_code']=int(re.search(r'exit code (-?\d+)',line).group(1))
                    events.append(event)
                    matched+=1
                    # Prefer lifecycle evidence to generic errors and rank incident proximity.
                    def rank(e):
                        raw=e.get('timestamp_utc') or e['timestamp_reported']
                        try:value=datetime.fromisoformat(raw.replace(',','.').replace('Z','+00:00')) if raw else None
                        except ValueError:value=None
                        value_clock=value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value
                        incident_clock=around.astimezone(timezone.utc).replace(tzinfo=None) if around else None
                        distance=abs((value_clock-incident_clock).total_seconds()) if incident_clock and value_clock else -value_clock.replace(tzinfo=timezone.utc).timestamp() if value_clock else float('inf')
                        return (0 if e['code'] in EVENTS else 1,distance)
                    events.sort(key=rank)
                    del events[24:]
            meta=next((f for f in manifest.get('files',[]) if f.get('file')==i.filename),{})
            coverage.append({'source':source,'file_index':file_number,'scanned':True,'first_timestamp_reported':stamps[0] if stamps else None,
                'last_timestamp_reported':stamps[-1] if stamps else None,'bytes_scanned':i.file_size,
                'truncated':meta.get('truncated') if isinstance(meta.get('truncated'),bool) else 'unknown','original_bytes':meta.get('original_bytes') if isinstance(meta.get('original_bytes'),int) else None})
            files+=1
    return {'machine_id':machine_id,'captured_utc':timestamp(str(manifest.get('captured_utc',''))), 'signals':{k:v for k,v in counts.items() if v}, 'files_scanned':files,
            'events':events,'matched_events':matched,'events_truncated':matched>24,'file_coverage':coverage[:24],
            'coverage_truncated':len(coverage)>24,
            'collection_omissions':dict(Counter('budget_exhausted' if f.get('skipped')=='Uncompressed collection budget exhausted' else 'file_unavailable' for f in manifest.get('files',[]) if f.get('skipped'))),
            'topic':topic,'requested_around_utc':around_utc or None,'collector_time_zone':str(zone) if zone else None,
            'event_ranking':'Lifecycle templates first, then proximity to requested wall-clock time (local timezone unverified) or latest reported time; bounded to 24 events.',
            'limitations':['Counts cover bounded archive tails, not necessarily the whole requested time range.',
                'Signals may repeat one incident. A zero count does not establish absence.',
                'Raw lines, account identities, secrets and hand data are never returned.']}


def read(db, scope, hours, *, machine_id=0, around_utc='', topic='all'):
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
    for machine in machines:
        mid=machine['MachineId']
        recent=db.rows("""SELECT TOP(4) r.SiteId,r.MachineId,r.BlobName,r.SizeBytes,r.CreatedUtc,r.Status,r.ExpiresUtc
            FROM backoffice.MachineLogRequest r
            WHERE r.CustomerId=%s AND r.MachineId=%s
            AND EXISTS(SELECT 1 FROM scheduler.Site s WHERE s.SiteId=r.SiteId AND s.CustomerId=r.CustomerId)
            AND r.ExpiresUtc>SYSUTCDATETIME() AND r.CreatedUtc>=DATEADD(hour,-%s,SYSUTCDATETIME())
            ORDER BY r.CreatedUtc DESC""",scope.customer_id,mid,hours)
        inventory.append({'machine_id':mid,'collections':[{k:v for k,v in r.items() if k not in ('BlobName','SizeBytes','MachineId','ExpiresUtc')} for r in recent[:2]]})
        ready=next((r for r in recent if r['Status']=='ready'),None)
        if ready:rows.append(ready)
    # Bound storage/network work. Narrow by machine to inspect other PCs.
    rows=rows[:1]
    results=[]
    if connection:
        storage=BlobServiceClient.from_connection_string(connection,connection_timeout=5,read_timeout=10,retry_total=0)
    else:
        from azure.identity import DefaultAzureCredential
        storage=BlobServiceClient(account_url=account_url,credential=DefaultAzureCredential(),connection_timeout=5,read_timeout=10,retry_total=0)
    with storage as client:
        for row in rows:
            if not row['SizeBytes'] or row['SizeBytes']>MAX_BYTES:continue
            blob=client.get_blob_client('machine-diagnostics',row['BlobName'])
            if blob.get_blob_properties().size>MAX_BYTES:continue
            results.append(summarize(blob.download_blob(max_concurrency=1).readall(),scope.site_id,row['MachineId'],around_utc=around_utc,topic=topic,archive_site_id=row['SiteId']))
    return {'inventory':inventory,'archives':results,'archive_limit':1,'machines_truncated':len(machines)==24,'limitations':['Reads completed unexpired archives; pending collections are listed. Recheck after collection completes. At most one machine archive per call; select machine_id for others. No raw text, paths, hand data or account identities are sent to the model. Machine-wide events cannot prove which site activity was affected.']}
