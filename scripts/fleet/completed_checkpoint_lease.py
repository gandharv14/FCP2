"""Same-owner renewal carrying an authentic completed instruction checkpoint.

The original lifecycle API owns the CAS. Historical readers authenticate archived
bytes; original I/W still validate the current owning checkpoint and lease.
"""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os

SCHEMA='fleet-completed-checkpoint-lease/v1'
FIELDS={'schema_version','implementation','renewal_module','bridge_module','instruction_module',
        'worker_module','authority','original_result','original_descriptor','before','after',
        'completion','descriptor','result','tracker_path','worker_run','worker_root','workbook','source'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    path=Path(path)
    require(path.is_absolute() and path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)),
            'regular immutable checkpoint artifact required')
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'checkpoint renewal bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def owning_predecessor(value):
    complete=read(value['checkpoint'])
    if value['status']=='STEP_COMPLETE':return complete
    require(complete.get('schema_version')=='harbor-dialogue-semantic-retry/v1',
            'only actual semantic RETRY_READY is supported')
    return dict(read(complete['permit']),lease=complete['lease'])


def snapshot(path,source):
    raw=Path(source).read_bytes()
    with Path(path).open('xb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)


def original(checkpoint,descriptor,I,W):
    value,selection,lease=I._prior(checkpoint,descriptor)
    worker,_,_=I.selected(descriptor)
    require(read(checkpoint)==value and value['descriptor']==descriptor
        and value['status'] in {'INSTRUCTION_READY','STEP_COMPLETE','RETRY_READY'} and value['next_step'] in W.STEPS
        and value['publication_performed'] is False,'actual completed owning checkpoint required')
    if value['status']=='INSTRUCTION_READY':
        require(value['checkpoint'] is None and value['next_step']==W.STEPS[0]
            and read(descriptor)['initial']==dict(selection=value['selection'],lease=value['lease']),
            'initial renewal cannot invent a worker predecessor')
        return value,worker,selection,lease
    predecessor=owning_predecessor(value)
    require(predecessor['selection']==value['selection'] and predecessor['lease']==asdict(lease),
            'completed owning predecessor differs')
    return value,worker,selection,lease


def projected(value,descriptor,selection,lease,I,evidence):
    return I._result(value['status'],descriptor,selection,lease,checkpoint=value['checkpoint'],
        next_step=value['next_step'],evidence=evidence)


def renew(checkpoint,descriptor,*,I,W,L,B,life,authority,directory,keep,validation,custody):
    """Caller holds the original workbook lock and supplies full owning history validation."""
    require(callable(validation) and callable(custody),'original validation and custody required')
    value,worker,selection,previous=original(checkpoint,descriptor,I,W)
    if value['status']=='INSTRUCTION_READY':
        require(not worker.run.exists(),'initial instruction already started')
    path=worker.context.tracker_root/(selection.content.workbook+'.json')
    W.active(worker,selection,previous);read(authority)
    directory=Path(directory)
    require(directory.is_absolute() and not directory.exists()
        and not any(p.is_symlink() for p in (directory,*directory.parents)),'unused renewal handoff required')
    directory.mkdir(parents=True)
    before=snapshot(directory/'before.json',path)
    require(before['sha256']==previous.sha256,'pre-renewal tracker changed')
    def checked(current,lease):
        require(lease==previous and current==read(before),'renewal validation custody changed')
        W.active(worker,selection,lease)
        return validation(worker,selection,lease,current)
    current,completion=L.renew(life=life,read=read,record=binding,keep=keep,path=path,
        lease=previous,authority_record=authority,validation=checked,custody=custody,
        directory=directory/'lease')
    after=snapshot(directory/'after.json',path)
    require(after['sha256']==current.sha256,'post-renewal tracker changed')
    base=dict(schema_version=SCHEMA,implementation=binding(__file__),renewal_module=binding(L.__file__),
        bridge_module=binding(B.__file__),instruction_module=binding(I.__file__),worker_module=binding(W.__file__),
        authority=authority,original_result=checkpoint,original_descriptor=descriptor,before=before,after=after,
        completion=completion,tracker_path=str(path),worker_run=str(worker.run),worker_root=str(worker.root),
        workbook=selection.content.workbook,source=selection.content.bindings['original_raw_source'])
    selected=keep(directory/'descriptor.json',dict(read(descriptor),
        origin=[*read(descriptor)['origin'],checkpoint,completion],
        initial=dict(selection=value['selection'],lease=I.base.encode(current))))
    result=keep(directory/'result.json',projected(value,selected,selection,current,I,[checkpoint,completion]))
    return keep(directory/'handoff.json',dict(base,descriptor=selected,result=result))


def validate(row,*,I,W,L,B,life):
    p=read(row)
    require(set(p)==FIELDS and p['schema_version']==SCHEMA
        and p['implementation']==binding(__file__)
        and all(p[k]==binding(m.__file__) for k,m in [
            ('renewal_module',L),('bridge_module',B),('instruction_module',I),('worker_module',W)]),
        'closed selected checkpoint renewal required')
    read(p['authority'])
    value,worker,selection,previous=original(p['original_result'],p['original_descriptor'],I,W)
    require(p['tracker_path']==str(worker.context.tracker_root/(selection.content.workbook+'.json'))
        and (p['worker_run'],p['worker_root'],p['workbook'],p['source'])==
        (str(worker.run),str(worker.root),selection.content.workbook,selection.content.bindings['original_raw_source']),
        'renewal changed worker or source')
    current=L.validate_completed(life=life,read=read,record=binding,path=p['tracker_path'],previous=previous,
        completion=p['completion'],before_binding=p['before'],after_binding=p['after'],authority_record=p['authority'])
    home=Path(row['path']).parent
    require(Path(row['path']).name=='handoff.json' and p['descriptor']==binding(home/'descriptor.json')
        and p['result']==binding(home/'result.json') and p['before']==binding(home/'before.json')
        and p['after']==binding(home/'after.json') and p['completion']==binding(home/'lease/complete.json'),
        'renewal handoff namespace differs')
    require(read(p['descriptor'])==dict(read(p['original_descriptor']),
        origin=[*read(p['original_descriptor'])['origin'],p['original_result'],p['completion']],
        initial=dict(selection=value['selection'],lease=I.base.encode(current)))
        and read(p['result'])==projected(value,p['descriptor'],selection,current,I,[p['original_result'],p['completion']]),
        'renewal handoff lost original checkpoint')
    return p,value,worker,selection,previous,current


@contextmanager
def installed(row,*,I,W,L,B,life,wrapper=None,directory=None):
    """Historical decode plus a single original predecessor lease predicate.

    Decoding never claims the archived after tracker is current. W.active in the
    original owning step remains live, including after later checkpoints advance.
    """
    old=I._prior
    def checked():
        I._prior=old
        try:return validate(row,I=I,W=W,L=L,B=B,life=life)
        finally:I._prior=selected
    def selected(checkpoint,descriptor):
        target=read(row)
        if checkpoint!=target['result']:return old(checkpoint,descriptor)
        p,value,worker,selection,previous,current=checked()
        require(descriptor==p['descriptor'],'renewal descriptor differs')
        return read(p['result']),selection,current
    def allowed(worker,selection,lease,checkpoint,predecessor):
        p,value,expected,original_selection,previous,current=checked()
        require(worker==expected and I.codec.encode(selection)==value['selection']
            and checkpoint==value['checkpoint'] and predecessor==owning_predecessor(value)
            and predecessor['lease']==asdict(previous) and lease==current,
            'renewal did not preserve exact original checkpoint')
        W.active(worker,selection,current)
        return True
    I._prior=selected
    try:
        _,value,*_=checked()
        if value['status']=='INSTRUCTION_READY':
            # The original first W.step creates its initial record with the
            # current lease. There is no historical predecessor to project.
            yield
        else:
            with B.step_scope(W,allowed,wrapper=wrapper,directory=directory):yield
    finally:I._prior=old
