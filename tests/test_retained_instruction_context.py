from scripts.fleet import retained_instruction_context as R,preserved_gate_continue as P
from contextlib import nullcontext
import pytest


def test_actual_preserved_context_only_replaces_checkpoint_hooks():
    result=R.projected(P.context,R.function_sha(P.context),lambda *a:None,lambda *a:nullcontext())
    assert result.__name__==P.context.__name__
    # The original scoped globals and source/control/current validation remain.
    fn=result.__wrapped__
    assert fn.__globals__['load'] is P.load
    assert fn.__globals__['completed_parent'] is P.completed_parent
    assert '_current_checkpoint' in fn.__globals__
    with pytest.raises(ValueError):R.projected(P.context,'0'*64,lambda *a:None,lambda *a:nullcontext())
