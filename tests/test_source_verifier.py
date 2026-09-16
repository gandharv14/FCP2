from pathlib import Path
import hashlib,importlib.util,json
import pytest
from scripts.fleet.source_verifier import preserve_verifier,verifier_names
from xl_seg.diagnostics import evidence_fingerprints

def files(extra=False):
 result={'xl_ast_graph.py':b'ast','xl_graph_store.py':b'graph','xl_segment.py':b'old main','xl_seg/publication.py':b'pub','xl_seg/diagnostics.py':b'diag','xl_seg/evaluate.py':b'eval','scripts/fleet/worker.py':b'worker'}
 if extra:result.update({'xl_seg/unused.py':b'unused module','xl_segment.py':b'new main','scripts/fleet/worker.py':b'current worker'})
 return result

def fingerprint(root,inventory):
 for name,raw in inventory.items():
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
 for name in ('book.xlsx','ast/nodes.csv','ast/edges.csv','curation.toml'):
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'stable input')
 return evidence_fingerprints(source_path=root/'book.xlsx',ast_dir=root/'ast',curation_path=root/'curation.toml',output_cells=['Sheet!A1'],verifier_paths=[root/n for n in verifier_names(inventory)])[0]['verifier_code']

def test_overlay_extra_module_and_entrypoint_change_actual_live_fingerprint(tmp_path):
 old=files();base=files(True);overlay={**base,**{k:v for k,v in old.items() if k.startswith('xl_seg/')}}
 expected=fingerprint(tmp_path/'old',old)
 assert fingerprint(tmp_path/'overlay',overlay)!=expected
 fixed=preserve_verifier(base,old)
 assert fingerprint(tmp_path/'selected',fixed)==expected
 assert fixed['scripts/fleet/worker.py']==base['scripts/fleet/worker.py']
 assert 'xl_seg/unused.py' not in fixed and fixed['xl_segment.py']==old['xl_segment.py']
 assert base==files(True) and old==files()

@pytest.mark.parametrize('missing',['xl_segment.py','xl_ast_graph.py','xl_seg/publication.py','xl_seg/diagnostics.py'])
def test_incomplete_source_verifier_cannot_be_selected(missing):
 old=files();old.pop(missing)
 with pytest.raises(ValueError):preserve_verifier(files(True),old)

@pytest.mark.parametrize('bad',['/absolute.py','../escape.py'])
def test_noncanonical_inventory_rejected(bad):
 old=files();old[bad]=b'bad'
 with pytest.raises(ValueError):preserve_verifier(files(True),old)

def test_actual_failed_generation_verifier_inventories_are_recovered_exactly():
 root=Path(__file__).resolve().parents[1];h=root/'diagnostics/vm-fleet-20260912/control';base=json.loads((h/'0036-source-successor-001/snapshot.json').read_bytes())
 def read(snapshot):
  values={k:(Path(snapshot['root'])/k).read_bytes() for k in snapshot['files']}
  assert all(hashlib.sha256(v).hexdigest()==snapshot['files'][k] for k,v in values.items())
  return values
 basebytes=read(base)
 for prior in (h/'0200-source-successor-001/snapshot.json',h/'paired-source-successor-004/snapshot.json'):
  old=read(json.loads(prior.read_bytes()));selected=preserve_verifier(basebytes,old)
  assert tuple((n,hashlib.sha256(selected[n]).hexdigest()) for n in verifier_names(selected))==tuple((n,hashlib.sha256(old[n]).hexdigest()) for n in verifier_names(old))
