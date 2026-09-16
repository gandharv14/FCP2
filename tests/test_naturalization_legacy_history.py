import json,types
import pytest
from scripts.fleet import naturalization_continue as N

def put(p,v):
 p.write_text(json.dumps(v));return N.binding(p)

@pytest.mark.parametrize('probe',[False,True])
def test_original_history_selection_precedes_dispatcher_without_new_authority(tmp_path,monkeypatch,probe):
 root=tmp_path/'code';root.mkdir();snapshot=put(tmp_path/'snapshot.json',{'root':str(root)})
 config=put(tmp_path/'config.json',{'code':snapshot});source={'path':'/source/0198.xlsx','sha256':'a'*64}
 worker=put(tmp_path/'worker.json',{'workbook':'0198','source':source,'version':'code'})
 helper=put(tmp_path/'helper.json',{});calls=[]
 monkeypatch.setattr(N,'module',lambda name,row:types.SimpleNamespace(select_history=lambda p,j,**kw:calls.append((p,j,kw))))
 monkeypatch.setattr(N.sys,'path',N.sys.path.copy())
 p=dict(workbook='0198',source=source,history_context=dict(helper=helper,original_worker_job=worker))
 N.select_history(p,{'config':config},probe=probe)
 assert calls==[(p,N.read(worker),{'probe':probe})]
 assert N.sys.path[0]==str(root)

@pytest.mark.parametrize('defect',['workbook','source','version','extra','changed'])
def test_history_context_rejects_foreign_or_changed_input_before_install(tmp_path,monkeypatch,defect):
 root=tmp_path/'code';root.mkdir();snapshot=put(tmp_path/'snapshot.json',{'root':str(root)});config=put(tmp_path/'config.json',{'code':snapshot});source={'path':'/source/0198.xlsx','sha256':'a'*64}
 job={'workbook':'0198','source':source,'version':'code'}
 if defect in ('workbook','version'):job[defect]='foreign'
 if defect=='source':job['source']={**source,'sha256':'b'*64}
 worker=put(tmp_path/'worker.json',job);context=dict(helper={},original_worker_job=worker)
 if defect=='extra':context['bypass']=True
 if defect=='changed':(tmp_path/'worker.json').write_text('{}')
 monkeypatch.setattr(N,'module',lambda *args:pytest.fail('foreign context installed'))
 with pytest.raises(ValueError):N.select_history(dict(workbook='0198',source=source,history_context=context),{'config':config})
