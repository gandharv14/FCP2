import copy
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_source_only_continuation as M
from scripts.batch.harbor_admission import record,AdmissionError


def empty_home(tmp_path):
    (tmp_path/'environment').mkdir();(tmp_path/'environment/selection.json').write_text('{}')
    (tmp_path/'bootstrap').mkdir();(tmp_path/'bootstrap/prepared.json').write_text('{}')
    return {'lane_state':{M.SOURCE:{'state':'passed'},'harbor-segmentation-analyst':{'state':'pending','attempt':0,'confidence_history':[],'current_confidence':None}}}


def test_downstream_consumption_or_uncertain_artifact_blocks_migration(tmp_path):
    tracker=empty_home(tmp_path);M.pristine(tmp_path,tracker)
    consumed=copy.deepcopy(tracker);consumed['lane_state']['harbor-segmentation-analyst']['attempt']=1
    with pytest.raises(AdmissionError,match='consumed'):M.pristine(tmp_path,consumed)
    (tmp_path/'environment/audit-plan.json').write_text('{}')
    with pytest.raises(AdmissionError,match='artifact'):M.pristine(tmp_path,tracker)


def test_even_empty_attempt_namespace_blocks_retry(tmp_path):
    tracker=empty_home(tmp_path);(tmp_path/'model-campaign').mkdir()
    with pytest.raises(AdmissionError,match='namespace'):M.pristine(tmp_path,tracker)


def test_actual_source_only_proof_retains_tracker_and_budget(monkeypatch):
    home=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0006-generated-01')
    if not home.exists():pytest.skip('optional retained0006 integration evidence')
    history=home.parents[2]/'vm-fleet-20260912/holds-0006/history-001.json'
    if history.exists():
        monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_HISTORY_PATH',str(history))
        monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_HISTORY_SHA256',record(history)['sha256'])
        monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0006')
        monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    tracker=record(home/'trackers/workbooks/0006.json')
    old_root=Path(json.loads(Path(json.loads((home/'config.json').read_text())['code']['path']).read_text())['root'])
    try:
        M.same_worker_source_boundary((old_root/'scripts/batch/harbor_environment_worker.py').read_text(),Path(M.C.worker.__file__).read_text())
    except AdmissionError:
        # The retained integration fixture predates independent worker changes.
        # It must remain refused by a current, incompatible source-only reader.
        with pytest.raises(AdmissionError,match='source/worker boundary'):
            M.inspect(record(home/'config.json'),record(home/'admission-001/descriptor.json'),record(home/'dispatcher-v2/first/000/result.json'),tracker)
        assert tracker==record(home/'trackers/workbooks/0006.json')
        return
    config,old,prepared,validated=M.inspect(record(home/'config.json'),record(home/'admission-001/descriptor.json'),record(home/'dispatcher-v2/first/000/result.json'),tracker)
    assert tracker==record(home/'trackers/workbooks/0006.json')
    assert old.normalization_budget.ordinal==1 and old.normalization_budget.limit==2
    assert prepared['validated']==validated and len(validated['targets']['ordered'])==3


def test_prospective_normalization_routing_cannot_change_source_or_setup():
    before='def _setup():\n    return 1\ndef _normalization():\n    return 2\n'
    routing='from scripts.batch import harbor_normalization_routing as normalization_routing\ndef _setup():\n    return 1\ndef _normalization():\n    return normalization_routing.produce()\n'
    M.same_worker_source_boundary(before,routing)
    with pytest.raises(AdmissionError,match='boundary'):M.same_worker_source_boundary(before,routing.replace('return 1','return 0'))


def test_takeover_layout_refuses_unsigned_path_only_migration(tmp_path):
    from scripts.batch import harbor_lease_takeover as T
    from scripts.batch.harbor_admission import read
    home=tmp_path/'migrated';home.mkdir()
    bad=home/'source-continuation.json';bad.write_text(json.dumps({'schema_version':M.SCHEMA,'destination':str(home),'tracker':'/foreign/tracker.json'}))
    config={'schema_version':'authoring-root-driver/v1','workbook':'0006','run_root':str(home),'source_continuation':record(bad)}
    cp=home/'config.json';cp.write_text(json.dumps(config))
    s=T.Selection(home,'0006',{},'environment',{}, {},'next',config=record(cp))
    with pytest.raises(AdmissionError,match='unsupported source-only'):T._layout(s)
