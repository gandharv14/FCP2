from pathlib import Path
import pytest
from scripts.fleet import fresh as f
from scripts.fleet import migration as m


def test_existing_reconstruction_preserves_spent_ledger_and_does_not_run(tmp_path, monkeypatch):
    root = tmp_path.resolve(); home = root / "home"; home.mkdir()
    ledger = m.retain(root / "ledger.json", {"build": {"attempts": 1, "state": "VERIFIED"}})
    source = m.retain(root / "source.json", {"historical_path": "/Users/original/source.xlsx"})
    segment = m.retain(root / "segment.json", {"historical_code_sha256": "f"*64})
    generated = m.retain(root / "generated.json", {"ledger": ledger})
    handoff = m.retain(root / "handoff.json", {"workbook": "0270", "state": "VERIFIED",
        "ledger": ledger, "source_manifest": source, "segmentation_manifest": segment})
    before = Path(ledger["path"]).read_bytes()
    monkeypatch.setattr(f, "reconstruction_child", lambda *_: pytest.fail("replayed consumed reconstruction"))
    plan = {"home": str(home), "workbook": "0270", "reconstruction": {
        "mode": "existing", "handoff": handoff, "generated_curation": generated}}
    selected, proof = f.reconstruct({}, plan)
    assert selected["ledger"] == ledger and proof == generated
    assert Path(ledger["path"]).read_bytes() == before
    assert m.checked(m.binding(home / "reconstruction-custody.json"))["reconstruction_executed"] is False


def test_existing_reconstruction_wrong_workbook_rejected(tmp_path):
    root = tmp_path.resolve(); home = root / "home"; home.mkdir()
    handoff = m.retain(root / "handoff.json", {"workbook": "0270", "state": "VERIFIED"})
    plan = {"home": str(home), "workbook": "0085", "reconstruction": {
        "mode": "existing", "handoff": handoff, "generated_curation": {}}}
    with pytest.raises(ValueError, match="original reconstruction"):
        f.reconstruct({}, plan)
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("change", [{"new_local_attempt_limit": 2}, {"inner_timeout_seconds": 1800},
                                    {"inner_memory_bytes": 16*1024**3}, {"models": True}])
def test_fresh_reconstruction_rejects_changed_consumption_or_guards(tmp_path, change):
    root = tmp_path.resolve(); home = root / "home"; home.mkdir()
    previous = {"selected_item": {"workbook_id": "0085"}, "new_local_attempt_limit": 1,
        "inner_timeout_seconds": 900, "inner_memory_bytes": 8*1024**3, "screen_timeout_seconds": 300,
        "outer_timeout_seconds": 1350, "models": False, "automatic_retry": False, **change}
    bound = m.retain(root / "prior-preparation.json", previous)
    plan = {"home": str(home), "workbook": "0085", "reconstruction": {"mode": "fresh",
        "prior_preparation": bound, "restriction_inventory": {}, "inventory_batch_id": "approved"}}
    with pytest.raises(ValueError, match="inherited reconstruction guards"):
        f.reconstruct({}, plan)
    assert list(home.iterdir()) == []


def inspection_fixture(tmp_path, monkeypatch):
    from test_fleet_history import certificate
    root, certificate_path = certificate(tmp_path, monkeypatch)
    sources=[]
    for wb in ("0001","0002"):
        source=tmp_path/(wb+".xlsx");source.write_bytes(wb.encode())
        sources.append(dict(workbook_id=wb,**m.binding(source)))
    inventory=m.retain(tmp_path/"inventory.json",dict(sources=sources))
    queue=m.retain(tmp_path/"queue.json",dict(items=[dict(workbook_id="0001",original_source=sources[0],
        history_class="no_history",preserved_history=[],preserved_curations=[])]))
    helper=tmp_path/"helper.py";helper.write_text("def run(*a): raise AssertionError('owning run forbidden')\ndef prepare(*a): raise AssertionError('owning prepare forbidden')\n")
    plan=dict(schema_version=f.SCHEMA,workbook="0001",queue=queue,inventory=inventory,
        history_roots=[str(root)],code_root=str(Path(f.__file__).resolve().parents[2]),
        home=str(tmp_path/"unused-home"),preparer=m.binding(helper),dispatcher=m.binding(helper),
        taxonomy=m.retain(tmp_path/"taxonomy.json",{}),env_file="/credentials/not-read",batch="test",owner="not-acquired",
        publication_root=str(tmp_path/"unused-publish"),output_root=str(tmp_path/"unused-output"),reconstruction={"mode":"fresh"})
    return plan,m.retain(tmp_path/"plan.json",plan),m.binding(certificate_path)


def test_actual_readonly_inspection_does_not_authorize_run_or_skip_live_heartbeat(tmp_path,monkeypatch):
    plan,selected,history=inspection_fixture(tmp_path,monkeypatch)
    def live_required(*a,**kw):raise RuntimeError("live custody required")
    monkeypatch.setattr('scripts.fleet.coordinator.Client',live_required)
    result=f.inspect(selected,history)
    assert result['status']=='FRESH_PLAN_INSPECTION_PASS' and result['custody_acquired'] is False
    assert result['admissions']==result['models_dispatched']==result['reconstructions']==0
    with pytest.raises(RuntimeError,match="live custody"):
        f.run(selected)
    assert not Path(plan['home']).exists()


def test_actual_inspection_rejects_stale_code_root(tmp_path,monkeypatch):
    plan,selected,history=inspection_fixture(tmp_path,monkeypatch)
    plan['code_root']=str(tmp_path);selected=m.retain(tmp_path/'stale-plan.json',plan)
    with pytest.raises(ValueError,match='selected code'):
        f.inspect(selected,history)
    assert not Path(plan['home']).exists()


def test_existing_reconstruction_retained_input_curation_keeps_all_attempts(tmp_path, monkeypatch):
    home=tmp_path/'home';home.mkdir();generation=tmp_path/'generation';generation.mkdir()
    original=tmp_path/'retained.toml';original.write_text('selected = ["S!A1"]\n')
    (generation/'curation.toml').write_bytes(original.read_bytes())
    history=[{'attempts':1,'state':'STOPPED','failure':'uncertified exact equation'}]
    ledger=m.retain(tmp_path/'ledger.json',dict(workbook_id='0010',build_history=history,
        build=dict(attempts=2,state='VERIFIED',curation_path=str(original),bindings=dict(curation_sha256=m.binding(original)['sha256']))))
    source=m.retain(tmp_path/'source.json',{});segment=m.retain(generation/'generation-manifest.json',{})
    handoff=m.retain(tmp_path/'handoff.json',dict(workbook='0010',state='VERIFIED',ledger=ledger,source_manifest=source,segmentation_manifest=segment))
    before=Path(ledger['path']).read_bytes()
    monkeypatch.setattr(f,'reconstruction_child',lambda *_:pytest.fail('source reconstruction replay'))
    selected,adoption=f.reconstruct({},dict(home=str(home),workbook='0010',reconstruction=dict(mode='existing',handoff=handoff,generated_curation=None)))
    assert adoption is None and selected['ledger']==ledger
    assert Path(ledger['path']).read_bytes()==before
    assert m.checked(ledger)['build_history']==history


@pytest.mark.parametrize('fault',['input_changed','output_changed','missing_input_binding'])
def test_existing_retained_curation_rejects_unbound_selection(tmp_path,fault):
    home=tmp_path/'home';home.mkdir();g=tmp_path/'g';g.mkdir();original=tmp_path/'retained.toml';original.write_text('original')
    (g/'curation.toml').write_text('changed' if fault=='output_changed' else 'original')
    sha=m.binding(original)['sha256']
    if fault=='input_changed':original.write_text('changed')
    ledger=m.retain(tmp_path/'ledger.json',dict(workbook_id='0010',build=dict(state='VERIFIED',curation_path=str(original),bindings=dict(curation_sha256=None if fault=='missing_input_binding'else sha))))
    source=m.retain(tmp_path/'source.json',{});segment=m.retain(g/'generation-manifest.json',{})
    handoff=m.retain(tmp_path/'handoff.json',dict(workbook='0010',state='VERIFIED',ledger=ledger,source_manifest=source,segmentation_manifest=segment))
    with pytest.raises(ValueError,match='exact input curation'):
        f.reconstruct({},dict(home=str(home),workbook='0010',reconstruction=dict(mode='existing',handoff=handoff,generated_curation=None)))
    assert list(home.iterdir())==[]
