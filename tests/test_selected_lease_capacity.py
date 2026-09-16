from pathlib import Path
import importlib.util,json
import pytest
from test_fleet_quarantine import setup
from scripts.fleet.migration import binding
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('selected_lease_capacity_test',ROOT/'diagnostics/bulk-production/replace_selected_lease.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
POLICY=ROOT/'diagnostics/bulk-production/model-quarantine-001/implementation-001/capacity_policy.py'

def plan(tmp,wb='0140',host='hhu-server-7'):
 p=tmp/'profile.json';p.write_text(json.dumps({'selection':{'owner':host+':slot-1:'+wb}}))
 return dict(schema_version='root-selected-lease/v2',workbook=wb,host=host,profile=binding(p),capacity_policy=binding(POLICY))

def test_only_exact_quarantine_unblocks_unrelated_takeover(tmp_path):
 c,now,claims,args=setup(tmp_path);p=plan(tmp_path)
 with pytest.raises(AssertionError):M.admission(c.status(),p)
 c.quarantine_model(**args);M.admission(c.status(),p)
 with pytest.raises(AssertionError):M.admission(c.status(),{**p,'schema_version':'root-selected-lease/v1'})
 now[0]=2000
 with pytest.raises(AssertionError):M.admission(c.status(),p)

@pytest.mark.parametrize('kind',['target','host','unknown','publication','active'])
def test_quarantine_does_not_bypass_other_owning_barriers(tmp_path,kind):
 c,now,claims,args=setup(tmp_path);c.quarantine_model(**args);p=plan(tmp_path)
 if kind=='target':p=plan(tmp_path,'0413','hhu-server')
 elif kind=='host':p=plan(tmp_path,'0140','hhu-server')
 elif kind=='unknown':
  c.begin('model','other-unknown',request_sha256='f'*64,**claims['0141']);c.end('model','other-unknown',evidence='uncertain',outcome='uncertain',**claims['0141'])
 elif kind=='publication':
  c.begin('publication','pub',request_sha256='f'*64,**claims['0140']);c.end('publication','pub',evidence='uncertain',outcome='uncertain',**claims['0140'])
 else:c.begin('model','target-active',request_sha256='f'*64,**claims['0140'])
 with pytest.raises((AssertionError,ValueError)):M.admission(c.status(),p)

def test_capacity_file_tamper_refused_before_import(tmp_path):
 c,now,claims,args=setup(tmp_path);p=plan(tmp_path);p['capacity_policy']['sha256']='0'*64
 with pytest.raises(AssertionError):M.admission(c.status(),p)
