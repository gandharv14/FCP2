from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import profile_derivation_remaining as R

def test_foreign_source_cannot_reuse_original_import():
    row={'path':'proof','sha256':'proof'};previous={'path':'previous','sha256':'previous'};helper={'path':R.__file__,'sha256':'helper'}
    old=dict(entry={'path':'old'},source={'sha256':'authentic'},checkpoint={'sha256':'import'},home='/run/old',operations='/ops/old',profile_recovery={'path':'old-policy'},profile_remaining={'path':'original-import-proof'})
    proof=dict(schema_version=R.SCHEMA,helper=helper,previous=previous,outer={},failed={},stopped={})
    p=dict(old,source={'sha256':'foreign'},entry={'path':'new'},home='/run/new',operations='/ops/new',profile_derived_remaining=row)
    def require(ok,message):
        if not ok:raise ValueError(message)
    E=SimpleNamespace(checked=lambda r:proof if r==row else old,binding=lambda p:helper,require=require)
    with pytest.raises(ValueError,match='original source or import'):R.validate(E,p)

@pytest.mark.parametrize('field',['checkpoint','profile_remaining','home','operations'])
def test_other_import_or_consumed_namespace_refused(field):
    row={'path':'proof'};previous={'path':'previous'};helper={'path':R.__file__};old=dict(entry={},source={},checkpoint={'sha256':'original'},profile_remaining={'path':'original'},home='/run/old',operations='/ops/old',profile_recovery={})
    proof=dict(schema_version=R.SCHEMA,helper=helper,previous=previous,outer={},failed={},stopped={});p=dict(old,entry={'path':'new'},home='/run/new',operations='/ops/new',profile_derived_remaining=row)
    p[field]=old[field]if field in ('home','operations')else{'path':'foreign'}
    def require(ok,message):
        if not ok:raise ValueError(message)
    E=SimpleNamespace(checked=lambda r:proof if r==row else old,binding=lambda p:helper,require=require)
    with pytest.raises(ValueError,match='original source or import'):R.validate(E,p)
