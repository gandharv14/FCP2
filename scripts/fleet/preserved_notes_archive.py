"""Read original failure notes against authentic submitted disclosure archives.

Only a completed repair validator's historical tracker pairs are projected.
The live notes validator and current task files remain unchanged outside it.
"""
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType,SimpleNamespace


def require(ok,message):
    if not ok:raise ValueError(message)


@contextmanager
def notes_scope(notes,trackers,mapped,checked):
    original=notes.validate_notes_pair
    lifecycle=SimpleNamespace(**vars(original.__globals__['lifecycle']))
    old_check=lifecycle.check_records
    def records(rows):
        selected=[]
        for row in rows:
            target=mapped.get((row.get('path'),row.get('sha256')))
            if target is not None:
                require(target['sha256']==row['sha256'],'historical notes archive bytes differ')
                checked(target);selected.append(target)
            else:selected.append(row)
        return old_check(selected)
    lifecycle.check_records=records;scope=dict(original.__globals__);scope['lifecycle']=lifecycle
    projected=FunctionType(original.__code__,scope,original.__name__,original.__defaults__,original.__closure__)
    def historical(tracker,raw):
        if any(tracker==value for value in trackers):return projected(tracker,raw)
        return original(tracker,raw)
    notes.validate_notes_pair=historical
    try:yield
    finally:notes.validate_notes_pair=original


@contextmanager
def installed(K,archive,receipt):
    original=K.validate
    def validate(W,row):
        from scripts.batch import harbor_lane_notes as notes,harbor_disclosure_round_adoption as A
        value=W.read(row);profile=W.read(value['profile']);adopted,mapped,_=archive.archives(A,receipt)
        require(value['implementation']==W.record(K.__file__)and adopted['source']==value['source']
            and Path(notes.__file__).resolve()==Path(value['worker_root'])/'scripts/batch/harbor_lane_notes.py',
            'historical notes reader differs from exact repaired source/code')
        transaction=W.read(profile['failure_transaction'])
        bindings=[transaction['before'],transaction['after'],*[value[k]for k in ('before_snapshot','after_snapshot','after_repair','before_renewal','after_renewal')]]
        trackers=[W.read(binding)for binding in bindings]
        require(all(v['workbook_id']==profile['workbook']and v['bindings']['original_raw_source']==value['source']for v in trackers),
            'foreign historical tracker cannot use submitted archives')
        # Full original adopted Call validation is owned by the enclosing S
        # context. archives independently checks submitted identity, every
        # mapping's equal SHA, and actual archive bytes on each invocation.
        with notes_scope(notes,trackers,mapped,A.E.checked_bytes):return original(W,row)
    K.validate=validate
    try:yield
    finally:K.validate=original
