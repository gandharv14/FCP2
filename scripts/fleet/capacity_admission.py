"""Root admission under the explicitly authorized concurrent model policy.

This validates each other active model's fenced workbook and known code/source.
It grants no model execution permission and cannot reconcile any operation.
"""
import math,re
from scripts.fleet.capacity_policy import validate_occupancy


def require(ok,message):
    if not ok:raise ValueError(message)


def operation_admission(status,job,closed_targets,known_others):
    require(status.get('schema_version')=='fleet-coordinator/v1','unknown coordinator schema')
    validate_occupancy(status)
    now=status.get('at');require(type(now)in(int,float)and math.isfinite(now),'invalid coordinator clock')
    require(isinstance(status.get('operations'),list)and isinstance(status.get('workbooks'),list),'unknown coordinator inventory')
    active=[];seen=set()
    for op in status['operations']:
        key=(op.get('kind'),op.get('operation_id'))
        require(isinstance(key[1],str)and key[1]and key not in seen,'duplicate or unknown operation identity');seen.add(key)
        require(op['state']!='hold','held operation requires reconciliation')
        if op['state']=='active':active.append(op)
    for op in active:
        require(job['workbook']in closed_targets,'unselected continued target')
        require(op['kind']=='model'and op.get('workbook')!=job['workbook'],'publication or target operation requires reconciliation')
        rows=[w for w in status['workbooks']if w.get('workbook')==op.get('workbook')]
        require(len(rows)==1,'exact active operation owner required');w=rows[0]
        require(w.get('state')=='active'and type(w.get('fence'))is int and type(op.get('fence'))is int and op['fence']>0
            and w.get('fence')==op['fence']and isinstance(op.get('owner'),str)and op['owner']
            and w.get('owner')==op['owner'],'operation claim custody mismatch')
        require(isinstance(w.get('claim_id'),str)and w['claim_id']and isinstance(w.get('version'),str)and w['version']
            and re.fullmatch('[0-9a-f]{64}',w.get('source_sha256',''))is not None
            and re.fullmatch('[0-9a-f]{64}',op.get('identity',''))is not None,'active claim identity incomplete')
        known=known_others.get(op.get('workbook'))
        require(known is not None and w['version']==known['version']and w['source_sha256']==known['source']['sha256'],'active operation is not a selected fleet job')
        require(op.get('ended')is None and op.get('evidence')is None,'active operation has terminal evidence')
        require(type(op.get('started'))in(int,float)and math.isfinite(op['started'])and op['started']<=now,'invalid operation start')
        for expiry in(op.get('expires'),w.get('expires')):
            require(type(expiry)in(int,float)and math.isfinite(expiry)and expiry>now,'expired or unknown lease requires reconciliation')
        require(op['expires']==w['expires'],'operation and claim lease disagree')
    return dict(schema_version='known-other-model-admission/v2',target=job['workbook'],central=status,
        active_other_models=active,model_limit=status['model_limit'],model_policy=status.get('model_policy'),publication_limit=1,
        model_permission_granted=False,unknown_operations_allowed=False)
