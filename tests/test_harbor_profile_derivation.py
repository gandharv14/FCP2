"""The derivation authority refuses altered declarations before model access."""
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_profile_derivation as D,harbor_profile_correction as Q
from scripts.batch.harbor_admission import record

def write(path,value):
    path.write_text(json.dumps(value));return record(path)

@pytest.fixture
def declaration(tmp_path,monkeypatch):
    artifact=tmp_path/'original.txt';artifact.write_text('retained immutable source evidence');item=record(artifact)
    q={k:item for k in D.FIELDS-{'schema_version'}}
    q.update(schema_version=D.SCHEMA,helper=record(Path(D.__file__).absolute()),authorization=dict(item,sha256=Q.AUTH))
    row=write(tmp_path/'declaration.json',q)
    monkeypatch.setattr(Q,'validate',lambda *a,**k:(_ for _ in()).throw(AssertionError('model ancestry must not be reached')))
    return tmp_path,q,row,artifact

@pytest.mark.parametrize('change',['foreign_helper','wrong_authority','extra_field','changed_source'])
def test_tampered_declaration_fails_before_model_ancestry(declaration,change):
    tmp,q,row,artifact=declaration
    if change=='foreign_helper':q['helper']=q['source']
    elif change=='wrong_authority':q['authorization']=q['source']
    elif change=='extra_field':q['accept_without_review']=True
    else:artifact.write_text('changed raw source evidence')
    row=write(tmp/'declaration.json',q)
    with pytest.raises(Exception)as error:D.validate(row,root=tmp)
    assert not isinstance(error.value,AssertionError)
