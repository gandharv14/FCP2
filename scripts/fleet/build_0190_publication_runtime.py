"""Prospective0190 runtime PATH/import correction, retaining prior publication code."""
from pathlib import Path
import json
import hashlib
import sys

ORIGINAL = Path(__file__).parents[2]/'diagnostics/vm-fleet-20260912/holds-0190/publication-successor-001/publish_0190.py'
ORIGINAL_SHA = 'f43078222a61ce4465d92770fe2568bdd0789fbe046686ea37af38265cca470b'
AUTHOR = dict(path=str(Path(__file__).parents[2]/'diagnostics/authoring-resume-20260910/runner/author_workbook.py'),
              sha256='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666')


def build(output):
    raw=ORIGINAL.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA:raise ValueError('original0190 publisher changed')
    out=Path(output).absolute();out.mkdir(parents=True,exist_ok=False)
    source=raw.decode()
    needle="    env = {k: v for k, v in os.environ.items() if not k.startswith('HARBOR_FLEET_')}\n"
    if source.count(needle)!=1:raise ValueError('0190 environment anchor differs')
    source=source.replace(needle,needle+"    runtime_bin = str(Path(sys.executable).absolute().parent)\n    env['PATH'] = runtime_bin + os.pathsep + (env.get('PATH') or os.defpath)\n")
    helper='''
AUTHOR_HELPER = %r
def retained_environment_binding(plan):
    ready=checked(plan['ready'])
    require(ready['status']=='PUBLICATION_READY' and ready['workbook']=='0190'
            and ready['publication_performed'] is False and ready['automatic_retry'] is False,
            'completed0190 remaining authoring required')
    row=ready['plan'];selection=checked(row)
    require(selection['schema_version']=='harbor-fleet-retained-environment-continuation/v1'
            and selection['workbook']=='0190' and selection['code_root']==plan['code_root']
            and selection['source_sha256']==plan['source_sha256']
            and selection['version']==Path(plan['code_root']).name
            and selection['config']==ready['config'], '0190 authoring lineage differs')
    authority=selection['history_authority']
    require(all(authority[k]==plan[k] for k in ('existing_review_history','existing_review_lineage','history_validator')),
            '0190 publication history detached from actual authoring')
    meta=checked(selection['namespace_adapter']);job=checked(ready['job'])
    require(ready['namespace_adapter']==selection['namespace_adapter']
            and job['driver']==meta['dispatcher'] and job['config']==ready['config']
            and meta['dispatcher'] in meta['files'] and meta['namespace'] in meta['files'],
            '0190 owning dispatcher namespace differs')
    for item in meta['files']:require(binding(item['path'])==item,'0190 namespace implementation changed')
    return row

def owning_dispatcher(plan, job):
    require(plan['workbook']=='0190', 'closed0190 publication runtime required')
    require(binding(AUTHOR_HELPER['path'])==AUTHOR_HELPER, '0190 publication author helper changed')
    cached=sys.modules.get('author_workbook')
    require(cached is None or binding(cached.__file__)==AUTHOR_HELPER, 'foreign cached0190 author helper')
    sys.modules['author_workbook']=load('author_workbook',AUTHOR_HELPER)
    return load('fleet_publication_dispatcher',job['driver'])

''' % AUTHOR
    needle='def remote_child(plan_binding):\n'
    if source.count(needle)!=1:raise ValueError('0190 remote anchor differs')
    source=source.replace(needle,helper+needle)
    needle='    driver = load("fleet_publication_dispatcher", job["driver"])'
    if source.count(needle)!=1:raise ValueError('0190 dispatcher anchor differs')
    source=source.replace(needle,'    driver = owning_dispatcher(plan, job)')
    needle="        env.update({prefix+'PATH': history['path'], prefix+'SHA256': history['sha256']})"
    if source.count(needle)!=1:raise ValueError('0190 namespace environment anchor differs')
    source=source.replace(needle,needle+"\n        if 'existing_review_history' in plan:\n            retained=retained_environment_binding(plan)\n            env.update(HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN=retained['path'], HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256=retained['sha256'])")
    needle="    if 'existing_review_history' in plan:\n        lineage = existing_review_adapter(plan).live(sys.modules[__name__], plan)"
    if source.count(needle)!=1:raise ValueError('0190 namespace child anchor differs')
    source=source.replace(needle,"    if 'existing_review_history' in plan:\n        retained=retained_environment_binding(plan)\n        require(os.environ.get('HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN')==retained['path'] and os.environ.get('HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256')==retained['sha256'], '0190 retained publication namespace differs')\n        lineage = existing_review_adapter(plan).live(sys.modules[__name__], plan)")
    def write(name,raw):
        p=out/name
        with p.open('xb') as f:f.write(raw if isinstance(raw,bytes) else raw.encode())
        return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    publisher=write('publish_0190.py',source);builder=write('build.py',Path(__file__).read_bytes())
    return write('manifest.json',json.dumps(dict(schema_version='fleet-0190-publication-runtime-successor/v1',
        original=dict(path=str(ORIGINAL),sha256=ORIGINAL_SHA),publisher=publisher,builder=builder,
        author_helper=AUTHOR,owning_publication_unchanged=True,history_validator_unchanged=True,
        task_code_unchanged=True,model_calls_allowed=False),indent=2,sort_keys=True)+'\n')


if __name__=='__main__':print(json.dumps(build(sys.argv[1])))
