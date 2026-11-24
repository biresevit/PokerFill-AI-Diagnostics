"""Compact model views preserve operational causes without exposing raw log content."""
import json

GENERIC_ERRORS={'RuntimeError','OSError','TimeoutError','ConnectionError','OperationalError'}
PRIORITY={'worker_heartbeat_stall':0,'heartbeat_timeout':1,'network_name_unavailable':2,
          'database_retry':2,'database_unreachable':2,'thread_creation_failed':2,
          'socket_resources_exhausted':2,'out_of_memory':2,'worker_site_restart':3,
          'supervisor_restart':3,'worker_ready':3,'worker_stopped':3,'chat_worker_epoch_lost':4}


def worker_view(item,limit):
    data=item.get('data',{})
    result={k:item[k] for k in ('id','source','captured_utc','site_id') if k in item}
    result['model_view_compacted']=True
    result['data']={k:data[k] for k in ('available','reason','machines_truncated') if k in data}
    result['data']['inventory']=data.get('inventory',[])[:2]
    archives=[]
    for archive in data.get('archives',[])[:1]:
        compact={k:archive[k] for k in ('machine_id','captured_utc','collector_time_zone',
            'from_utc','to_utc','scan_time_limit_reached','coverage_truncated') if k in archive}
        groups=archive.get('event_groups',[])
        specific=[g for g in groups if g.get('code') not in GENERIC_ERRORS]
        chosen=sorted(specific or groups,key=lambda g:PRIORITY.get(g.get('code'),5))[:limit]
        compact['event_groups']=[]
        for group in chosen:
            g={k:group[k] for k in ('code','source','site_attribution','count','phase','first_utc','last_utc') if k in group}
            if 'first_utc' not in g:
                g.update({k:group[k] for k in ('clock','first_reported','last_reported') if k in group})
            if group.get('first_details'):g['first_details']=group['first_details']
            if group.get('last_details') and group['last_details']!=group.get('first_details'):
                g['last_details']=group['last_details']
            compact['event_groups'].append(g)
        compact['events_omitted']=bool(archive.get('events') or len(chosen)<len(groups) or archive.get('event_groups_truncated'))
        # Older parsers/fixtures may provide only detailed events.
        if not groups:
            compact['events']=[{k:e[k] for k in ('code','source','site_attribution','timestamp_utc',
                'timestamp_reported','clock','phase','seconds_without_heartbeat') if k in e}
                for e in archive.get('events',[])[:limit]]
        coverage={}
        for file in archive.get('file_coverage',[]):
            source=file.get('source','unknown')
            row=coverage.setdefault(source,{'source':source,'files_scanned':0,'scan_incomplete':False,
                                           'truncated_or_unknown':False})
            row['files_scanned']+=bool(file.get('scanned'))
            row['scan_incomplete']|=bool(file.get('scan_incomplete'))
            row['truncated_or_unknown']|=file.get('truncated') is not False
            for key,operation in (('first_timestamp_reported',min),('last_timestamp_reported',max)):
                value=file.get(key)
                if value:row[key]=operation(row.get(key,value),value)
        compact['file_coverage']=list(coverage.values())[:4]
        compact['limitations']='Partial scanned evidence; timestamp extents do not prove continuous coverage. Match counts are not distinct incidents. Omitted events remain in full evidence.'
        archives.append(compact)
    result['data']['archives']=archives
    return result


def model_evidence(item):
    import tiktoken
    encoding=tiktoken.get_encoding('o200k_base')
    def fits(value):
        return len(encoding.encode(json.dumps(value,default=str,ensure_ascii=False,separators=(',',':')),
                                   disallowed_special=()))<=900
    if item.get('source')=='get_worker_log_signals':
        for limit in (8,4,3):
            candidate=worker_view(item,limit)
            if fits(candidate):return candidate
    def trim(value,count,chars):
        if isinstance(value,dict):return {key:trim(v,count,chars) for key,v in value.items()}
        if isinstance(value,list):return [trim(v,count,chars) for v in value[:count]]
        return value[:chars] if isinstance(value,str) else value
    for count,chars in ((24,400),(8,240),(3,120)):
        candidate=trim(item,count,chars)
        if candidate!=item:candidate['model_view_compacted']=True
        if fits(candidate):return candidate
    return {'id':item.get('id'),'source':item.get('source'),
            'data':{'available':False,'reason':'Evidence was too large for model context; full bounded evidence is retained.'}}
