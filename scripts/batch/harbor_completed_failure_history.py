"""Recompute an actual owning failure through its original notes mutation.

This is a historical comparison only: it grants neither a lease nor permission
for a worker to ignore the retained failure.
"""
from pathlib import Path

def validate(*,notes,codec,context,transaction,completion):
    d=notes.read(transaction);tx=Path(transaction['path']).parent;wb=d['workbook']
    notes.require(set(d)=={'schema_version','workbook','before','after','notes','prior_notes','packet','packet_v2'}
        and d['schema_version']=='harbor-notes-transaction/v1'
        and Path(transaction['path'])==tx/'transaction.json'
        and tx.parent==context.tracker_root/(wb+'-notes-transactions'), 'foreign completed failure transaction')
    for key,name in [('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')]:
        if d[key] is not None:
            notes.require(Path(d[key]['path'])==tx/name,'failure transaction member escaped')
            notes.lifecycle.checked_record(d[key])
    notes.require(completion==notes.record(tx/'complete.json')
        and notes.read(completion)==dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256']),
        'owning failure completion differs')
    before=notes.read(d['before']);after=notes.read(d['after'])
    for value in (before,after):notes.schema.validate_tracker(value);notes.lifecycle.validate_lifecycle(value)
    notes.require(before['workbook_id']==after['workbook_id']==wb and after['revision']==before['revision']+1,
                  'foreign owning failure tracker')
    packet=notes.GateEvidence(**d['packet'])if d['packet']is not None else codec.decode(d['packet_v2'])
    result=notes.check_gate(before,packet,context)
    notes.require(result['passed']is False and result['terminal']is False,'recorded failure is not the same repairable owning rejection')
    lease=before['leases']['task_agent'];notes.require(lease['heartbeat_at']<=after['updated_at']<lease['expires_at'],
                                                    'failure was recorded outside original live custody')
    notespath=context.tracker_root/(wb+'-agent-notes.md')
    rebuilt,rendered=notes._failure_after(before,result,after['updated_at'],notespath)
    notes.require(rebuilt==after and Path(d['notes']['path']).read_bytes()==rendered,
                  'owning failure differs from original validator and exact mutation')
    notes.validate_notes_pair(after,rendered)
    if d['prior_notes']is not None:notes.validate_notes_pair(before,Path(d['prior_notes']['path']).read_bytes())
    else:notes.require(before['agent_notes']['path']is None,'failure omitted prior synchronized notes')
    return dict(transaction=transaction,completion=completion,before=d['before'],after=d['after'],
                result=result,selection=codec.encode(packet.selection),models_dispatched=0,task_mutations=0)
