"""Read an explicit finite chain of completed queued lease transitions."""
from pathlib import Path
import hashlib,importlib.util,sys
SCHEMA='harbor-queued-publication-history/v1'
KINDS={'harbor-queued-publication-lease/v1','harbor-expired-queued-publication/v1'}

def require(ok,message):
    if not ok:raise ValueError(message)

def projected(P,request,completion):
    value=P.read(completion)
    require(set(value)=={'schema_version','workbook','source_sha256','steps'} and value['schema_version']==SCHEMA,
            'closed queued lease history required')
    require(value['workbook']==request.selection.content.workbook and isinstance(value['source_sha256'],str)
            and len(value['source_sha256'])==64,'queued history source identity differs')
    steps=value['steps'];require(isinstance(steps,list) and 2<=len(steps)<=6,'finite queued history required')
    seen=set();selected=[]
    # Authenticate the entire declared closure before invoking any reader.
    for step in steps:
        require(set(step)=={'reader','completion'},'closed queued history step required')
        row=step['reader'];P.lifecycle.checked_record(row);v=P.read(step['completion'])
        require(v.get('schema_version') in KINDS,'only original queued transitions may compose')
        profile=P.read(v['intent'])['profile']
        require(profile['workbook']==value['workbook'] and profile['source_sha256']==value['source_sha256'],
                'queued history transition source differs')
        identity=(step['completion']['path'],step['completion']['sha256'])
        require(identity not in seen,'queued history repeats a transition');seen.add(identity)
        selected.append((row,step['completion'],v['schema_version']))
    for row,bound,kind in selected:
        path=Path(row['path']);require(hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'queued history reader changed')
        name='_queued_history_'+row['sha256'];spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
        require(module.SCHEMA==kind,'queued history reader kind differs')
        previous=request;request=module.projected(P,request,bound)
        require(request.selection==previous.selection and request.tracker_root==previous.tracker_root
                and request.lease.revision>previous.lease.revision,'queued history lost monotonic custody')
    return request
