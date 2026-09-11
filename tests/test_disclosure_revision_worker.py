from dataclasses import replace
from pathlib import Path
import pytest
from scripts.batch import harbor_instruction_worker as w
from scripts import harbor_instruction_campaign as C
from scripts.batch import harbor_nat_retry as N,harbor_dialogue_semantic_retry as S
from test_instruction_worker import case,instruction_setup

def test_optional_revision_codec_namespace_and_retry_history(case):
    worker,*_=case
    assert 'disclosure_revision' not in C.worker_encode(worker)
    old=w.keep(worker.run/'old/permit.json',{'old':'retained'})
    revision=w.keep(worker.run/'revision/permit.json',{'schema_version':'harbor-disclosure-revision/v1','predecessor':old,'prior_continuations':{'disclosure_repair':old}})
    selected=replace(worker,disclosure_repair=old,disclosure_revision=revision)
    assert C.worker_decode(C.worker_encode(selected))==selected
    assert w.step_directory(selected,'disclosure-build')==worker.run/'steps/disclosure-build'
    assert w.step_directory(selected,'disclosure-review')==Path(revision['path']).parent/'steps/disclosure-review'
    assert w.step_directory(selected,'enqueue')==Path(revision['path']).parent/'steps/enqueue'
    assert N._prior_continuations(selected)['disclosure_revision']==revision
    assert S._continuations(selected)['disclosure_revision']==revision
    forged=w.keep(worker.run/'forged.json',{'prior_continuations':{}})
    with pytest.raises(ValueError):C.worker_encode(replace(selected,disclosure_revision=forged))
