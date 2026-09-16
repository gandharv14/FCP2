import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import recursion_solver_policy as p


def test_policy_replaces_all_auxiliary_routing_and_preserves_image():
    source={'harnessImageUrl':'immutable-image','envVars':{'ANTHROPIC_DEFAULT_HAIKU_MODEL':'haiku','OTHER':'keep'}}
    result=p.enforce(source)
    p.validate(result)
    assert result['harnessImageUrl']=='immutable-image'
    assert result['envVars']['OTHER']=='keep'
    assert source['envVars']['ANTHROPIC_DEFAULT_HAIKU_MODEL']=='haiku'


@pytest.mark.parametrize('field',['args',*p.ENV])
def test_stale_configuration_is_rejected(field):
    config=p.enforce({})
    if field=='args':config['args']='--model claude-opus-5'
    else:config['envVars'][field]='wrong'
    with pytest.raises(ValueError):p.validate(config)


def test_other_models_cannot_be_submitted():
    with pytest.raises(ValueError):p.validate_models(['opus','gemini'])
