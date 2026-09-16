import copy,hashlib,importlib.util,json,os,types
from pathlib import Path
import pytest
from scripts.fleet import publication_0029_guard as G
from scripts.fleet.build_0029_publication import build

def state():
    selected={'sha256':'a'*64};auth=dict(workbook='0029',owner='host:publication',token='private',fence=3)
    identity=hashlib.sha256(json.dumps(dict(workbook='0029',request_sha256=selected['sha256']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    row=dict(workbook='0029',owner=auth['owner'],fence=3,source_sha256=G.SOURCE,version=G.CODE,state='active',expires=150)
    op=dict(kind='publication',workbook='0029',owner=auth['owner'],fence=3,state='active',expires=150,identity=identity)
    return dict(model_limit=1,publication_limit=1,at=100,workbooks=[row],operations=[op]),auth,selected

def test_exact_live_publication_identity():
    s,a,p=state();assert G.validate_status(s,a,Path('/code')/G.CODE,p,now=100)['version']==G.CODE

@pytest.mark.parametrize('field,value',[('owner','other'),('fence',2),('version','old'),('source_sha256','0'*64),('state','hold'),('expires',99)])
def test_wrong_claim(field,value):
    s,a,p=state();s['workbooks'][0][field]=value
    with pytest.raises(ValueError):G.validate_status(s,a,Path('/code')/G.CODE,p,now=100)

@pytest.mark.parametrize('field,value',[('kind','model'),('owner','other'),('fence',2),('identity','0'*64),('expires',99),('state','hold'),('state','unknown')])
def test_wrong_operation(field,value):
    s,a,p=state();s['operations'][0][field]=value
    with pytest.raises(ValueError):G.validate_status(s,a,Path('/code')/G.CODE,p,now=100)

def test_parallel_model_even_other_workbook_blocks():
    s,a,p=state();s['operations'].append({**s['operations'][0],'kind':'model','workbook':'0013'})
    with pytest.raises(ValueError):G.validate_status(s,a,Path('/code')/G.CODE,p,now=100)

def test_no_operation_does_not_authorize():
    s,a,p=state();s['operations']=[]
    with pytest.raises(ValueError):G.validate_status(s,a,Path('/code')/G.CODE,p,now=100)

def test_publication_context_never_reads_model_journal(monkeypatch):
    monkeypatch.setenv('HARBOR_FLEET_PUBLICATION_ONLY','1');monkeypatch.delenv('HARBOR_FLEET_MODEL_JOURNAL',raising=False);monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    for k,v in dict(URL='http://127.0.0.1:1',TOKEN_FILE='/runtime/token',WORKBOOK='0029',OWNER='pub',CLAIM_TOKEN='secret',FENCE='3').items():monkeypatch.setenv('HARBOR_FLEET_'+k,v)
    from scripts.fleet import coordinator
    monkeypatch.setattr(coordinator,'Client',lambda *a:('client',a))
    client,auth=G.context();assert auth['fence']==3
    monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL','/old/model')
    with pytest.raises(ValueError):G.context()

def test_both_separately_loaded_namespaces_receive_exact_hook(monkeypatch):
    n1=types.SimpleNamespace(__file__='/ns.py');n2=types.SimpleNamespace(__file__='/ns.py');dispatcher={'path':'/dispatcher.py','sha256':'d'}
    m={'namespace_module':{'path':'/ns.py','sha256':'n'},'dispatcher':dispatcher}
    monkeypatch.setattr(G,'adapter',lambda:m);monkeypatch.setattr(G,'binding',lambda p:m['namespace_module'])
    pub={'namespace':n1,'load':lambda name,row:types.SimpleNamespace(namespace=n2)}
    G.install_loader(pub);d=pub['load']('fleet_publication_dispatcher',dispatcher)
    assert n1.install_execution_guards is G.install and d.namespace.install_execution_guards is G.install
    with pytest.raises(ValueError):pub['load']('fleet_publication_dispatcher',{'path':'other'})

def test_sealed_publisher_uses_successor_child_and_retains_old_history_check(tmp_path):
    old=G.adapter();oldbytes=Path(old['publication_driver']['path']).read_bytes();m=G.checked(build(tmp_path/'pub'))
    spec=importlib.util.spec_from_file_location('independentpub',m['publication_driver']['path']);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert Path(module.__file__)==Path(m['publication_driver']['path'])
    assert module.namespace.__file__==old['namespace_module']['path']
    assert module.namespace.install_execution_guards is module.publication_guard.install
    driver=module.load('fleet_publication_dispatcher',old['dispatcher'])
    assert driver.namespace is not module.namespace
    assert driver.namespace.install_execution_guards is module.publication_guard.install
    assert Path(old['publication_driver']['path']).read_bytes()==oldbytes
    assert module.namespace.publication_history.__code__.co_filename==old['namespace_module']['path']
