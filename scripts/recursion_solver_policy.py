"""Permanent FCP solver policy; reject stale or mixed-model submissions."""
import copy

MODELS = ('opus',)
MODEL_ID = 'claude-opus-5'
ARGS = '--model claude-opus-5 --effort max'
ENV = {key: MODEL_ID for key in (
    'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
    'ANTHROPIC_SMALL_FAST_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL')}
ENV['CLAUDE_CODE_EFFORT_LEVEL'] = 'max'


def enforce(config):
    result = copy.deepcopy(config)
    result['args'] = ARGS
    result.setdefault('envVars', {}).update(ENV)
    return result


def validate(config):
    if config.get('args') != ARGS or any(config.get('envVars', {}).get(k) != v for k, v in ENV.items()):
        raise ValueError('FCP evaluations require only claude-opus-5 with max effort, including auxiliary models')


def validate_models(models):
    if tuple(models) != MODELS:
        raise ValueError('Only Opus 5 max is authorized for FCP evaluations')
