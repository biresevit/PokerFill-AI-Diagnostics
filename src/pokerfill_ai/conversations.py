"""Bounded conversation context; full transcripts remain in the private history."""
import json

MAX_TURNS = 20
CONTEXT_TURNS = 4
CONTEXT_CHARS = 16000


def context(rows):
    turns=[]
    selected=rows if len(rows)<=CONTEXT_TURNS else [rows[0],*rows[-(CONTEXT_TURNS-1):]]
    for row in selected:
        result=json.loads(row['result']) if row['result'] else None
        report=(result or {}).get('report') or {'summary':'This attempt failed; no diagnosis was produced.','findings':[],'limitations':[]}
        # Keep earlier evidence's source/clock, not large raw database payloads.
        turns.append({'request_id':row['id'],'created_utc':row['created_utc'],
            'question':row['prompt'][:2000], 'status':row['status'],'mode':(result or {}).get('mode','unknown'),
            'failure':str(row['error'])[:500] if row['status']=='failed' and 'error' in row.keys() and row['error'] else None,
            'answer':{'summary':str(report.get('summary',''))[:1200],
                'findings':[{'title':str(f.get('title',''))[:160],
                    'explanation':str(f.get('explanation',''))[:600],
                    'certainty':f.get('certainty','unknown')} for f in report.get('findings',[])[:6]],
                'limitations':[str(x)[:300] for x in report.get('limitations',[])[:6]]},
            'evidence_sources':[{'source':e.get('source'),'captured_utc':e.get('captured_utc')}
                for e in (result or {}).get('evidence',[])[:12]]})
    # Keep the original diagnosis and the latest exchange as anchors. Drop
    # middle exchanges first, then compact text if Unicode/long reports need it.
    while len(turns)>2 and len(json.dumps(turns))>CONTEXT_CHARS:
        turns.pop(1)
    def compact(value,limit):
        if isinstance(value,str):return value[:limit]
        if isinstance(value,list):return [compact(v,limit) for v in value[:2]]
        if isinstance(value,dict):return {k:compact(v,limit) for k,v in value.items()}
        return value
    for limit in (300,150,75):
        if len(json.dumps(turns))<=CONTEXT_CHARS:break
        turns=[compact(t,limit)|{'context_truncated':True} for t in turns]
    return turns
