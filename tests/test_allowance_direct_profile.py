"""Cold direct custody selection uses real retained code and scoped policy."""
from pathlib import Path
import copy,json,subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('change',['valid','source','expired','duration','code','foreign_imports'])
def test_real_profile_fails_before_custody_mutation(tmp_path,change):
    home=ROOT/'diagnostics/bulk-production/0303-continuation'
    if not(home/'takeover-profile.json').exists():pytest.skip('retained native profile fixture unavailable')
    p=json.loads((home/'takeover-profile.json').read_bytes())
    tracker=json.loads((home.parent/'0303-custody-001.json').read_bytes())['tracker']['value']
    if change=='source':tracker['bindings']['original_raw_source']['sha256']='f'*64
    target=tmp_path/'tracker.json';target.write_text(json.dumps(tracker))
    import hashlib
    def binding(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    p['selection']['tracker']=binding(target)
    if change=='duration':p['selection']['duration_ns']+=1
    if change=='code':
        snapshot=json.loads(Path(p['original_snapshot']['path']).read_bytes());snapshot['files'][0]['sha256']='e'*64
        row=tmp_path/'snapshot.json';row.write_text(json.dumps(snapshot));p['original_snapshot']=binding(row)
    profile=tmp_path/'profile.json';profile.write_text(json.dumps(p));before=target.read_bytes()
    program='''import importlib.util,sys,json
from pathlib import Path
root=Path(sys.argv[1]); row=json.loads(sys.argv[2]);mode=sys.argv[3]
spec=importlib.util.spec_from_file_location('direct_candidate',root/'scripts/batch/harbor_allowance_takeover.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
if mode=='foreign_imports':
 sys.path.insert(0,str(root));import scripts.batch
if mode=='expired':m.time.time_ns=lambda:10**30
try:
 p,T,s,w=m.load(row)
 assert mode=='valid','invalid direct profile accepted'
 assert s.workbook=='0303'and s.allowance==p['allowance']and s.duration_ns==14400*10**9
except Exception:
 if mode=='valid':raise
print('PASS')
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',program,str(ROOT),json.dumps(binding(profile)),change],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='PASS'
    assert target.read_bytes()==before
