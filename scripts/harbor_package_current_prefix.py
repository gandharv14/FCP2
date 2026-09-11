"""Current owning prefix proof for the exact partition executor applicability.

Read-only validation. Historical code is data; all current financial/content
checks run. This does not modify prior checkpoints or approve a tracker lane.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy
from scripts.batch import harbor_environment_refresh as F,harbor_environment_worker as W
from scripts.batch import harbor_normalization_partitioned as P,harbor_partition_code_applicability as A
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_transition_codec import decode,encode
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA='harbor-current-partition-package-prefix/v1'


def _map(records):
    out={}
    for item in records:
        require(set(item)=={'path','sha256'},'exact evidence record required')
        require(item['path'] not in out or out[item['path']]==item['sha256'],'ambiguous evidence hash')
        out[item['path']]=item['sha256']
    return out


def _roles(content):
    require(content.normalization_prepared is not None and content.normalization_call is None,'exact completed partition context required')
    current=P.validate_content(content)
    prepared=read(content.normalization_prepared)
    profiles=A.profiles(P.codes())
    matching=[v for v in profiles.values() if v==prepared['code']]
    require(len(matching)==1,'retained preparation is not an approved historical profile')
    require(all(current['checked_files'].get(k)==v for k,v in P.codes().items()),'current independent partition proof lacks code closure')
    return matching[0],current


def _checked(item,witnesses,roles):
    try:F.checked(item,witnesses)
    except ValueError:
        require(roles.get(item['path'])==item['sha256'] and P.codes().get(item['path'])!=item['sha256'],'changed non-code or unapproved old code evidence')
        # _roles has already proven the exact old source archives and complete
        # current JSON Calls, budgets, request/context and aggregate equality.


def _result(old,new,roles):
    require(set(old)==set(new) and {k:v for k,v in old.items() if k!='evidence'}=={k:v for k,v in new.items() if k!='evidence'},'current prefix changed a gate result outside code evidence')
    before,after=_map(old['evidence']),_map(new['evidence']);current=P.codes();added=A.code_records()
    for path,sha in before.items():
        if after.get(path)==sha:continue
        require(roles.get(path)==sha and current.get(path)==after.get(path),'prefix changed or dropped non-code evidence')
    for path,sha in after.items():
        if path not in before:require(added.get(path)==sha,'prefix introduced unrelated evidence')
    return dict(original_evidence=old['evidence'],current_evidence=new['evidence'])


def inspect(s,descriptor,checkpoint,before,current,*,allow_environment=False):
    from scripts import harbor_campaign as C
    d=read(descriptor);binding=d.get('refresh');require(binding is not None,'root-selected completed F context required')
    selected,_,_=C.selected(descriptor);require(selected==s,'descriptor selected a different worker')
    prior=read(checkpoint)
    require(prior.get('descriptor')==descriptor and prior.get('status')=='STEP_COMPLETE' and prior.get('step')=='normalization_phase2' and decode(prior['selection'])==current,'exact completed phase2 result required')
    roles,partition=_roles(current.content)
    v=read(binding)
    require(set(v)=={'schema_version','code','worker','prior','checkpoint','original_checkpoints','before','selection','last','results','evidence','semantic_budget_reset','mutable_witnesses'} and v['semantic_budget_reset'] is False,'retained F schema/budget changed')
    require(v['schema_version']==F.SCHEMA and v['code']==record(F.__file__) and v['worker']==record(s.run/'selection.json') and read(v['worker'])['selection']==W.bootstrap.serial(asdict(s)),'retained F owner/worker code changed')
    original=decode(v['selection']);witnesses=v['mutable_witnesses']
    require(len({w['key'] for w in witnesses})==len(witnesses) and all(w['key'] in F.MUTABLE for w in witnesses),'invalid managed witnesses')
    for w in witnesses:
        require(Path(w['original']['path'])==original.content.normalization_run/F.MUTABLE[w['key']] and Path(w['copy']['path'])==Path(binding['path']).parent/'generated-before'/F.MUTABLE[w['key']],'foreign managed witness')
        F.checked(w['original'],witnesses)
    for item in [v['before'],v['prior'],v['checkpoint'],*v['original_checkpoints'],*v['evidence'],*prior['evidence']]:_checked(item,witnesses,roles)
    previous=read(v['prior']);kind=previous.get('step')
    require(kind in F.KINDS and previous.get('status') in {'STEP_COMPLETE','ENVIRONMENT_READY'},'retained predecessor is not complete')
    old=decode(previous['selection']);saved=read(v['checkpoint'])
    require(v['checkpoint']==record(s.run/(kind+'.json')) and saved['selection']==previous['selection'],'retained predecessor checkpoint differs')
    for item in previous['evidence']+saved['evidence']:_checked(item,witnesses,roles)
    delegated=F.unchanged(old,original)
    require(v['last']==('normalization_phase1' if delegated else kind),'completed prefix scope changed')
    old_before=read(v['before']);require(all(before[k]==old_before[k] for k in ('raw_source','generation_ids')),'source or generation changed')
    later=Path(binding['path']).parent/'continuation/normalization_phase2.json'
    require(later.is_file() and v['last']=='normalization_phase1','exact retained phase2 continuation required')
    phase2=read(record(later));latest=decode(phase2['selection'])
    require(latest==current,'root selected another current phase2 task')
    profile=read(record(later.parent/'source_profile.json'));profile_selection=decode(profile['selection'])
    require(profile_selection.public_profiles==current.public_profiles,'lost exact profile review')
    for item in profile['evidence']:_checked(item,witnesses,roles)
    for item in phase2['evidence']:_checked(item,(),roles)
    require(current.content.normalization_run==original.content.normalization_run and current.content.normalization_call==original.content.normalization_call and current.content.normalization_prepared==original.content.normalization_prepared,'phase2 changed normalization Calls or identity')
    for key,val in original.content.bindings.items():
        if key not in F.MUTABLE:require(current.content.bindings.get(key)==val,'phase2 changed prior financial/evidence binding')
    replacements={w['original']['path']:current.content.bindings[w['key']] for w in witnesses}
    def mapped(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'} and value['path'] in replacements:return replacements[value['path']]
            return {k:mapped(x) for k,x in value.items()}
        if isinstance(value,list):return [mapped(x) for x in value]
        return value
    expected=mapped(v['results'])
    projected=replace(original,content=replace(original.content,bindings={**original.content.bindings,**{k:current.content.bindings[k] for k in F.MUTABLE}}))
    fresh=F.checks(s,old_before,projected,v['last'])
    require(len(fresh)==len(expected),'retained prefix gate count changed')
    comparisons=[_result(a,b,roles) for a,b in zip(expected,fresh)]
    require(v['evidence']==[item for result in v['results'] for item in result['evidence']],'retained F evidence inventory changed')
    complete=F.checks(s,before,current,'normalization_phase2')
    if not allow_environment:require(not (later.parent/'environment.json').exists(),'later environment checkpoint cannot be discarded')
    evidence=[descriptor,checkpoint,binding,v['worker'],v['before'],v['prior'],v['checkpoint'],*v['original_checkpoints'],record(later),record(later.parent/'source_profile.json')]
    evidence.extend(dict(path=k,sha256=h) for k,h in partition['checked_files'].items())
    return dict(schema_version=SCHEMA,descriptor=descriptor,checkpoint=checkpoint,refresh=binding,selection=encode(current),
        source_identity={k:before[k] for k in ('raw_source','generation_ids')},historical_results=v['results'],current_original_prefix_results=fresh,current_complete_prefix_results=complete,
        code_evidence_comparisons=comparisons,original_code=roles,current_code=P.codes(),evidence=W.lanes.notes.evidence_list(evidence),
        implementation=record(__file__),tracker_mutations=0,semantic_budget_reset=False)


def validate(receipt,s,descriptor,checkpoint,before,current,*,allow_environment=False):
    expected=inspect(s,descriptor,checkpoint,before,current,allow_environment=allow_environment)
    require(read(receipt)==expected,'current prefix applicability receipt altered or stale')
    return expected
