from pathlib import Path
import json
from types import SimpleNamespace
import pytest
import xl_macro_source_history as H
from xl_source_cache_equivalence import binding,object_hash

@pytest.fixture
def retained(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir();(root/'xl_macro_source_projection.py').write_text('# frozen original validator\n')
    records={'xl_macro_source_projection.py':binding(root/'xl_macro_source_projection.py')['sha256']}
    fingerprint=object_hash(records);(root/'.pipeline-code.json').write_text(json.dumps(records));target=tmp_path/fingerprint;root.rename(target);root=target
    snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps(dict(root=str(root),fingerprint=fingerprint,parent=None,bindings={x.name:binding(x)for x in root.iterdir()})))
    registry=tmp_path/'registry.json';registry.write_text(json.dumps(dict(schema_version=H.SCHEMA,snapshots={fingerprint:binding(snapshot)})))
    monkeypatch.setattr(H,'REGISTRY',registry);monkeypatch.setattr(H,'_CACHE',{})
    for name in('source','candidate','proof','authorization'):(tmp_path/name).write_text(name)
    policy=tmp_path/'policy';policy.write_text(json.dumps(dict(authorization=binding(tmp_path/'authorization'))))
    manifest=dict(implementation_sha256=fingerprint,source=binding(tmp_path/'source'),candidate=binding(tmp_path/'candidate'),proof=binding(tmp_path/'proof'),policy=binding(policy))
    result=dict(original_sha256=manifest['source']['sha256'],candidate_sha256=manifest['candidate']['sha256'],macros_executed=False,source_qualified=False)
    calls=[]
    def run(*a,**kw):calls.append((a,kw));return SimpleNamespace(returncode=0,stdout=json.dumps(result),stderr='')
    monkeypatch.setattr(H.subprocess,'run',run)
    return tmp_path,root,manifest,calls


def test_cached_history_requires_exact_code_and_every_input(retained):
    tmp,root,manifest,calls=retained
    first=H.verify(tmp/'source',tmp/'candidate',manifest)
    assert H.verify(tmp/'source',tmp/'candidate',manifest)==first and len(calls)==1
    (tmp/'proof').write_text('tampered')
    with pytest.raises(ValueError,match='input changed'):H.verify(tmp/'source',tmp/'candidate',manifest)
    assert len(calls)==1


def test_cached_history_rejects_changed_original_code(retained):
    tmp,root,manifest,calls=retained;H.verify(tmp/'source',tmp/'candidate',manifest)
    (root/'xl_macro_source_projection.py').write_text('changed')
    with pytest.raises(ValueError,match='closure changed'):H.verify(tmp/'source',tmp/'candidate',manifest)
    assert len(calls)==1


def test_unregistered_implementation_never_executes(retained):
    tmp,root,manifest,calls=retained;manifest['implementation_sha256']='0'*64
    with pytest.raises(ValueError,match='not explicitly retained'):H.verify(tmp/'source',tmp/'candidate',manifest)
    assert calls==[]
