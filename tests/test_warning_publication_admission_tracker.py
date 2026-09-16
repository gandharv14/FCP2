import json,hashlib
from pathlib import Path
import pytest
from scripts.fleet.retained_warning_publication import direct_inputs

def test_retained_admission_tracker_is_not_current_tracker(tmp_path):
    def b(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
    def put(p,v):p.parent.mkdir(exist_ok=True,parents=True);p.write_text(json.dumps(v));return b(p)
    def read(r):assert b(r['path'])==r;return json.loads(Path(r['path']).read_text())
    def require(ok,msg):
        if not ok:raise ValueError(msg)
    source='a'*64;before={'workbook_id':'0291','revision':22,'bindings':{'original_raw_source':{'sha256':source}},'leases':{'task_agent':{'owner':'owner'}}}
    tracker=tmp_path/'tracker.json';original=put(tracker,before);home=tmp_path/'warning';copy=put(home/'semantic-live-before.json',before);authority={'path':'/plan','sha256':'b'*64}
    receipt={'schema_version':'fleet-retained-live-continuation-lease/v1','authority':authority,'tracker':original,'validated_evidence':[copy],'lease_operations':0,'automatic_retry':False,'semantic_budget_reset':False,'lease':{'sha256':original['sha256'],'revision':22,'owner':'owner'}}
    put(home/'live-renewal/retained-live.json',receipt);put(tracker,dict(before,revision=26));prior={'workbook':'0291','source_sha256':source,'output':str(home),'live_renewal':{'completed_semantic':{'tracker':original}}};job={'inputs':[original]}
    direct_inputs(authority,prior,job,checked=read,binding=b,require=require)
    for change in ('authority','tracker','archive','lease','unknown_input'):
        altered=dict(receipt);changed_job=job
        if change=='authority':altered['authority']={}
        if change=='tracker':altered['tracker']=b(tracker)
        if change=='archive':altered['validated_evidence']=[]
        if change=='lease':altered['lease']=dict(receipt['lease'],revision=999)
        if change=='unknown_input':changed_job={'inputs':[dict(original,path=str(tmp_path/'other'))]}
        put(home/'live-renewal/retained-live.json',altered)
        with pytest.raises((ValueError,FileNotFoundError)):
            direct_inputs(authority,prior,changed_job,checked=read,binding=b,require=require)
    put(home/'live-renewal/retained-live.json',receipt);put(home/'semantic-live-before.json',dict(before,revision=99))
    with pytest.raises(ValueError,match='archive differs'):
        direct_inputs(authority,prior,job,checked=read,binding=b,require=require)
