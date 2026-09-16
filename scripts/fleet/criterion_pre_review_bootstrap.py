"""Data-selected cold runtime for a deterministic pre-review criterion repair.

Every original module remains byte-identical. The context keeps the declared
reader installed throughout an owning child, then restores all module aliases.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib, importlib, importlib.util, json, os, sys

SCHEMA='fleet-criterion-pre-review-runtime/v1'
MODULES=('source_card_authoring','source_card_profile','source_card_readiness',
         'criterion_source_cards','criterion_draft_correction','criterion_pre_review',
         'criterion_pre_review_reader','criterion_pre_review_authorize','criterion_pre_review_install')


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path)
    require(p.is_absolute() and not any(q.is_symlink()for q in (p,*p.parents)), 'canonical runtime path required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
    require(type(row)is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'runtime evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'runtime module changed')
    old=sys.modules.get(name)
    require(old is None or binding(old.__file__)==row,'foreign cached runtime: '+name)
    if old is not None:return old
    spec=importlib.util.spec_from_file_location(name,row['path']);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value
    try:spec.loader.exec_module(value)
    except BaseException:sys.modules.pop(name,None);raise
    if '.'in name:
        parent,leaf=name.rsplit('.',1);setattr(sys.modules[parent],leaf,value)
    return value


def inventory(snapshot):
    value=checked(snapshot);root=Path(value['root'])
    expected={row['relative']:row['sha256']for row in value['files']}
    require(len(expected)==len(value['files']),'duplicate original inventory path')
    actual={}
    for p in root.rglob('*'):
        require(not p.is_symlink(),'symlink in original task code')
        if p.is_file() and '__pycache__'not in p.parts:actual[str(p.relative_to(root))]=binding(p)['sha256']
    require(actual==expected,'full original task inventory changed')
    return root


def renderers(profile,P,worker,selected):
    """Load corrected renderers under private names, restoring skill aliases."""
    old_extract=P.skill_module(worker.root,'additional-assumptions-dialogue','extract_claims')
    old=sys.modules.get('spoken_formula')
    require(old is not None,'original spoken renderer was not selected')
    previous={key:sys.modules.get(key)for key in ('spoken_formula','extract_claims')}
    try:
        new=module('criterion_selected_spoken',selected['spoken_formula'])
        sys.modules['spoken_formula']=new
        extract=module('criterion_selected_extract',selected['extract_claims'])
    finally:
        for key,value in previous.items():
            if value is None:sys.modules.pop(key,None)
            else:sys.modules[key]=value
    import openpyxl
    claims=checked(checked(profile['original_pair'])['original_claims']);formulas={};original={}
    for row,values in ((profile['source'],original),(profile['published_source'],formulas)):
        require(binding(row['path'])==row,'formula source changed')
        book=openpyxl.load_workbook(row['path'],read_only=True,data_only=False)
        try:
            for claim in claims['claims']:
                for cell in claim['reviewer_only']['cells']:
                    sheet,address=cell.rsplit('!',1);values[cell]=book[sheet.strip("'").replace("''","'")][address].value
        finally:book.close()
    for change in checked(profile['changes']):
        require(original[change['source_cell']]==formulas[change['source_cell']]==change['source_formula'],
                'criterion formula differs from original source')
    return lambda content:(old,new,extract,formulas)


@contextmanager
def context(bound,*,probe):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode and type(probe)is bool,'fresh -I -B runtime required')
    p=checked(bound)
    require(set(p)=={'schema_version','workbook','profile','manifest','stdlib','original_authoring_job',
        'renderers'} and p['schema_version']==SCHEMA,'closed criterion runtime required')
    manifest=checked(p['manifest'])
    require(set(manifest)=={'schema_version','modules','execution'} and
        manifest['schema_version']=='fleet-criterion-runtime-inventory/v1' and
        set(manifest['modules'])==set(MODULES)|{'criterion_pre_review_bootstrap'},'closed helper inventory required')
    require(manifest['modules']['criterion_pre_review_bootstrap']==binding(__file__),'unselected runtime loader')
    for row in [*manifest['modules'].values(),*manifest['execution'].values(),*p['renderers'].values()]:
        require(binding(row['path'])==row,'runtime dependency changed')
    require(set(p['renderers'])=={'spoken_formula','extract_claims'},'exact criterion renderers required')
    stdlib=module('criterion_runtime_stdlib',p['stdlib']);stdlib.preload_inspect()
    import asyncio,dataclasses
    os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','')
    profile=checked(p['profile']);require(profile['workbook']==p['workbook'],'runtime workbook changed')
    root=inventory(profile['snapshot']);sys.path.insert(0,str(root));importlib.import_module('scripts.fleet')
    for name in MODULES:module('scripts.fleet.'+name,manifest['modules'][name])
    from scripts.fleet import criterion_pre_review_reader as reader_module,criterion_pre_review_install as install
    driver_job=checked(p['original_authoring_job']);driver=module('criterion_original_dispatcher',driver_job['driver'])
    if probe:os.environ['HARBOR_FLEET_CONTINUATION_READ_ONLY']='1'
    else:require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','diagnostic cannot enter owning runtime')
    env=driver.setup(driver_job)
    require(env[6]==root and env[5]['workbook']==p['workbook'] and driver_job['publish']is False,
            'original dispatcher source or capabilities differ')
    originals={k:importlib.import_module(name)for k,name in install.NAMES.items()}
    from scripts.batch import harbor_instruction_lease_renewal as renewal,harbor_lease_takeover as takeover
    from scripts.batch import harbor_dialogue_authoring as authors,harbor_transition_codec as codec
    C=originals['campaign'];W=originals['worker'];P=originals['producer']
    worker=C.worker_decode(checked(profile['old_descriptor'])['worker'])
    permit=Path(profile['directory'])/'permit.json'
    authority=binding(permit)if permit.exists()else dict(path=str(permit),sha256='0'*64)
    reader=reader_module.Reader(p['profile'],authority,W=W,P=P,C=C,R=renewal,T=takeover,A=authors,
        codec=codec,renderers=renderers(profile,P,worker,p['renderers']),
        validator=P.skill_module(root,'additional-assumptions-dialogue','validate_dialogue'))
    for row in [p['stdlib'],*manifest['modules'].values(),*manifest['execution'].values(),*p['renderers'].values()]:
        require(row in reader.dependencies(),'unselected cold execution dependency')
    with install.installed(originals,reader)as selected:
        selected['renewal']=renewal
        yield p,reader,selected,driver,env
