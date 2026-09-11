"""A historical profile fixture must not imply active expanded code is allowed."""
import pytest
from scripts.batch import harbor_partition_code_applicability as A
from scripts.batch import harbor_normalization_partitioned as P


def test_expanded_live_modules_require_the_explicit_active_snapshot_bridge():
    # The legacy profile is deliberately closed. It must not silently authorize
    # the new producer validation bridge or disclosure physical-repair executor.
    with pytest.raises(ValueError,match='unreviewed transport executor delta|partition producer changed'):
        A.profile(P.codes())
