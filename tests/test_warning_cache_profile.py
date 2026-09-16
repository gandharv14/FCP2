import json
from types import SimpleNamespace
from pathlib import Path
import pytest
from scripts.fleet import retained_warning_continue as M

def test_old_q_keeps_original_worker_active():
    calls=[];w=object();s=object();l=object();W=SimpleNamespace(active=lambda *a:calls.append(a)or 'original')
    assert M.preparation_active({'Q':SimpleNamespace()},W,w,s,l)=='original'
    assert calls==[(w,s,l)]

def test_selected_q_preparation_gate_is_preserved():
    calls=[];Q=SimpleNamespace(preparation_active=lambda *a:calls.append(a)or 'selected');p=dict(Q=Q,old_snapshot={'path':'old'})
    assert M.preparation_active(p,None,1,2,3)=='selected' and calls==[(1,2,3,p['old_snapshot'])]

def test_cache_profile_is_bound_to_warning_source_and_both_snapshots(tmp_path,monkeypatch):
    monkeypatch.delenv('HARBOR_SOURCE_CACHE_HISTORY',raising=False)
    profile=dict(original_snapshot={'path':'old'},current_snapshot={'path':'new'},original_sha256='a'*64)
    path=tmp_path/'p.json';path.write_text(json.dumps(profile));row=M.binding(path)
    plan=dict(cache_lineage_profile=row,old_snapshot=profile['original_snapshot'],snapshot=profile['current_snapshot'],source_sha256='a'*64)
    M.select_normalization_profile(plan,None)
    import os
    assert json.loads(os.environ['HARBOR_SOURCE_CACHE_HISTORY'])==row
    for key in ['source_sha256','snapshot','old_snapshot']:
        with pytest.raises(ValueError):M.select_normalization_profile({**plan,key:'foreign'},None)
