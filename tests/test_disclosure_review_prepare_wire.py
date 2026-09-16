from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_review_prepare as P
from scripts.batch import harbor_disclosure_partition_wire as W


def test_original_default_and_explicit_pinned_encodings():
 assert P.selected_wire_encoding({},W)==W.SCHEMA
 for value in (W.SCHEMA,W.COLUMNAR_SCHEMA,W.RUN_SCHEMA):assert P.selected_wire_encoding({'wire_encoding':value},W)==value
 assert P.selected_wire_encoding({},SimpleNamespace(SCHEMA=W.SCHEMA))==W.SCHEMA

@pytest.mark.parametrize('value',[None,False,'gzip','lossless-workbook-shard-pair/v99'])
def test_unsupported_or_opaque_encoding_rejected(value):
 with pytest.raises(ValueError):P.selected_wire_encoding({'wire_encoding':value},W)

def test_old_selected_module_cannot_silently_use_new_encoder():
 with pytest.raises(ValueError):P.selected_wire_encoding({'wire_encoding':W.RUN_SCHEMA},SimpleNamespace(SCHEMA=W.SCHEMA))
