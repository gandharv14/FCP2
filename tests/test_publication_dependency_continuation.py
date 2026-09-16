"""A missing pinned dependency may be completed without altering owning code."""
import json
from pathlib import Path
import pytest
from scripts.fleet import publication_finalization_launch as F


def put(path, value):
    path.write_text(json.dumps(value));return F.binding(path)


def source(path, value):
    path.write_text(value);return F.binding(path)


@pytest.mark.parametrize('defect',[None,'publisher','status','capacity','inventory'])
def test_dependency_completion_has_exact_code_boundary(tmp_path,defect):
    capacity=source(tmp_path/'capacity.py','LIMIT = 2\n')
    oldstatus=source(tmp_path/'old-status.py',f"CAPACITY_POLICY = None\nCAPACITY_POLICY_SHA = {capacity['sha256']!r}\ndef check(): return 1\n")
    newstatus=source(tmp_path/'new-status.py',f"CAPACITY_POLICY = {capacity!r}\nCAPACITY_POLICY_SHA = {capacity['sha256']!r}\ndef check(): return {2 if defect=='status' else 1}\n")
    oldpub=source(tmp_path/'old-pub.py',f"publication_status=load('publication_status',{oldstatus!r})\ndef own(): return 'original'\n")
    newpub=source(tmp_path/'new-pub.py',f"publication_status=load('publication_status',{newstatus!r})\ndef own(): return {'changed' if defect=='publisher' else 'original'!r}\n")
    oldmanifest=dict(publisher=oldpub,dependencies=dict(publisher=oldpub,publication_status=oldstatus),unchanged=True)
    if defect=='capacity':capacity=source(tmp_path/'capacity2.py','LIMIT = 1000\n')
    newmanifest=dict(publisher=newpub,dependencies=dict(publisher=newpub,publication_status=newstatus,capacity_policy=capacity),unchanged=True)
    if defect=='inventory':newmanifest['dependencies']['extra']=capacity
    old=dict(publisher=oldpub,implementation=put(tmp_path/'old.json',oldmanifest))
    p=dict(publisher=newpub,implementation=put(tmp_path/'new.json',newmanifest))
    p['dependency_continuation']=put(tmp_path/'continuation.json',dict(schema_version='publication-status-dependency-continuation/v1',prior_implementation=old['implementation'],implementation=p['implementation']))
    if defect:
        with pytest.raises(ValueError):F.dependency_continuation(p,old)
    else:assert F.dependency_continuation(p,old) is True
