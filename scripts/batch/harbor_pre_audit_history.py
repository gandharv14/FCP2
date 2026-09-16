"""Read-only proof of a retained pre-audit reconstruction boundary.

Historical code is hashed as data, never imported. This does not authorize a
reconstruction retry or erase its prior local attempts. Normal source, lease,
model, and publication gates remain mandatory after migration.
"""
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA='harbor-pre-audit-history/v1'
DRIVER_SHA='e1e1e63dd95e7b4ae86781b58cc3c4e33de3db762c8c3e83518fdca397ae3979'
EARLY={'source_health','immutable_source_ast','segmentation_generation','segmentation_verification','segmentation_strict_live_pass','strict_segmentation_validation'}
FIELDS={'ast_path','curation','diagnostics','first_blocker','gates','mode','promotion_attempted','segmentation_generation_id','source_candidate','source_file','source_generation_id','source_health','source_root','staged_path','status','workbook_id','verification','segmentation_verification','provenance_reconciliation','segmentation_attempted_generation_id','checks','segmentation_candidate_path','updated_at'}
LATE=re.compile(r'audit|variable.sources|normaliz|disclosure|naturaliz|request|response|additional.assumptions|maskability|leakscan|baseline.inputs',re.I)

def require(value,message):
    if not value:raise ValueError(message)

def binding(path):
    from scripts.batch.harbor_tracker import safe_path
    p=safe_path(path);require(p==p.resolve() and p.is_file(),'historical evidence missing or lexical alias')
    return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

def checked(b):
    require(isinstance(b,dict) and set(b)=={'path','sha256'},'closed evidence binding required')
    require(binding(b['path'])==b,'historical evidence bytes changed')
    return Path(b['path'])

def read(b):return json.loads(checked(b).read_text())

def local_source_request(path,source_sha):
    if Path(path).name!='request.json' or not Path(path).is_file():return False
    try:value=read(binding(path))
    except (ValueError,OSError):return False
    keys={'mode','policy_version','request_id','request_sha256','schema_version','source_sha256'}
    if set(value)!=keys or value.get('schema_version')!='source-identity-request/v2' or value.get('mode')!='no_recalculation_required' or value.get('policy_version')!='source-recalc-policy/v3' or value.get('source_sha256')!=source_sha:return False
    from xl_source_publication import _binding_hash
    unsigned=dict(value);claimed=unsigned.pop('request_sha256')
    return claimed==_binding_hash(unsigned) and value['request_id']=='identity-'+source_sha[:24]

def related(roots,wb,known_ids,source_sha):
    from scripts.batch.harbor_admission import PRUNE
    found=[]; pattern=re.compile(r'(?<!\d)'+re.escape(wb)+r'(?:[-_.]|$)')
    def owner(part):
        # Exact range-named cohort directories do not own either endpoint.
        if re.fullmatch(r'(?:[A-Za-z_-]+)?\d{4}-\d{4}(?:-\d{8})?',part):return None
        match=re.match(r'^(\d{4})(?:[-_.]|$)',part)
        return match[1] if match and match[1] in known_ids else None
    def model_scope(value,path_context):
        declared=set();scopes=[]
        def visit(v,context):
            if isinstance(v,dict):
                local={o for k,x in v.items() if k in {'workbook','workbook_id'}
                       and isinstance(x,str) and (o:=owner(Path(x).name))}
                declared.update(local)
                # Each request inherits only its enclosing object's declared
                # identity. An unrelated sibling cannot supply its budget owner.
                current=(local|path_context) if local else context
                if isinstance(v.get('messages'),list) or 'api_calls' in v:
                    scopes.append(current)
                for x in v.values():visit(x,current)
            elif isinstance(v,list):
                for x in v:visit(x,context)
        visit(value,path_context);return declared,scopes
    for root in roots:
        for base,dirs,files in os.walk(root,followlinks=False):
            base=Path(base);path_owners={o for p in base.relative_to(root).parts if (o:=owner(p))}
            scope=wb in path_owners
            for name in dirs+files:
                p=base/name
                if scope or owner(name)==wb:
                    require(not p.is_symlink(),'related historical symlink requires preserved-identity review')
                    require(not LATE.search(name) or local_source_request(p,source_sha),'post-segmentation evidence requires model-budget reconciliation: '+str(p))
            dirs[:]=sorted(d for d in dirs if d not in PRUNE)
            for name in sorted(files):
                p=base/name
                if not p.is_file():continue
                declared=set();model_scopes=[]
                path_context=path_owners|({owner(name)} if owner(name) else set())
                if name.endswith('.json') and not p.is_symlink():
                    try:declared,model_scopes=model_scope(json.loads(p.read_text()),path_context)
                    except (json.JSONDecodeError,UnicodeError):pass
                owners=path_owners|declared|({owner(name)} if owner(name) else set())
                for scope_owners in model_scopes:
                    require(bool(scope_owners),'unscoped historical model envelope requires diagnosis: '+str(p))
                    require(wb not in scope_owners,'discovered model consumption requires explicit budget inheritance: '+str(p))
                if scope or owner(name)==wb or wb in declared or (pattern.search(name) and not owners):
                    found.append(binding(p))
    return sorted({b['path']:b for b in found}.values(),key=lambda b:b['path'])


def physical_aliases(records):
    groups={}
    for b in records:
        p=checked(b);st=p.stat();groups.setdefault((st.st_dev,st.st_ino),[]).append(b['path'])
    return sorted([sorted(set(paths)) for paths in groups.values() if len(set(paths))>1])

def alias_data(rows,driver):
    from scripts.batch.harbor_tracker import safe_path
    repo=checked(driver).parents[2]
    permitted={str(repo/'synthetic-data-pipeline'/name):str(repo/name)
               for name in ('runs','seg_out','source_out','inputs_out')}
    require(isinstance(rows,list) and len({x.get('path') for x in rows})==len(rows),'closed historical alias list required')
    for row in rows:
        require(set(row)=={'path','target','canonical_root'} and permitted.get(row['path'])==row['canonical_root'],'unapproved historical namespace alias')
        p=Path(row['path']);safe_path(p.parent);target=safe_path(row['canonical_root'])
        require(p.is_symlink() and str(p.readlink())==row['target'] and p.resolve()==target and target.is_dir(),'historical namespace alias changed')
    return rows

def canonical_record(path,aliases):
    p=Path(path).absolute()
    for row in aliases:
        alias=Path(row['path'])
        if alias in p.parents:
            p=Path(row['canonical_root'])/p.relative_to(alias);break
    return binding(p)

def project(*,workbook,original,tracker,curations,driver,history_roots,known_ids,historical_path_aliases=None):
    from scripts.batch.harbor_admission import survey_namespaces
    require(re.fullmatch(r'\d{4}',workbook) and workbook in known_ids,'unknown workbook')
    from scripts.batch.harbor_tracker import safe_path
    roots=tuple(safe_path(x) for x in history_roots)
    require(roots and len(set(roots))==len(roots) and all(p.is_dir() and not p.is_symlink() for p in roots),'complete history roots required')
    require(all(p==p.resolve() for p in roots) and len({(p.stat().st_dev,p.stat().st_ino) for p in roots})==len(roots),'aliased history roots')
    require(not any(a in b.parents for a in roots for b in roots if a!=b),'overlapping history roots require explicit coverage reconciliation')
    aliases=alias_data(historical_path_aliases or [],driver)
    source=checked(original);require(source.stem==workbook,'original identity mismatch')
    d=read(tracker);require(set(d)<=FIELDS,'unknown legacy tracker fields require diagnosis')
    require(d.get('workbook_id')==workbook and d.get('source_candidate')==str(source),'legacy original mismatch')
    require(d.get('status')=='blocked' and d.get('mode') is None and d.get('staged_path') is None and d.get('promotion_attempted') is False,'historical task was not stopped before authoring')
    require((d.get('first_blocker') or {}).get('gate') in EARLY,'positive pre-audit terminal boundary required')
    gates=d.get('gates');require(isinstance(gates,dict),'legacy gates missing')
    for name,value in gates.items():
        if name not in EARLY:
            require(name=='preflight_through_step_15_5' and isinstance(value,dict) and value.get('status')=='not_run' and d.get('checks',{}).get('later_gates_attempted') is False,'later gate evidence cannot be reset')
    health=d.get('source_health',{});require(health.get('source_sha256')==original['sha256'],'legacy source digest mismatch')
    c=d.get('curation',{});require(c.get('preserved') is True and c.get('recurate_used') is False and c.get('sha256_before')==c.get('sha256_after'),'historical curation must remain unchanged')
    require(curations and len({x['path'] for x in curations})==len(curations),'retained curation bindings required')
    canonical_curations=[]
    for b in curations:
        require(set(b)=={'path','sha256'},'closed historical curation binding')
        actual=canonical_record(b['path'],aliases);require(actual['sha256']==b['sha256']==c.get('sha256_after'),'retained curation differs')
        canonical_curations.append(actual)
    checked(driver);require(driver['sha256']==DRIVER_SHA,'unreviewed historical driver profile')
    diagnostics=d.get('diagnostics');require(isinstance(diagnostics,list) and diagnostics,'positive local diagnostic history required')
    retained=[canonical_record(p,aliases) for p in diagnostics]
    excluded,histories=survey_namespaces(roots,set(known_ids))
    require(workbook not in excluded,'prior task identity cannot migrate through pre-audit proof')
    require(histories.get(workbook)==[tracker],'all historical evidence must be exactly the pre-audit tracker')
    census=related(roots,workbook,set(known_ids),original['sha256'])
    return {'schema_version':SCHEMA,'workbook':workbook,'original':original,'tracker':tracker,'curations':curations,'canonical_curations':canonical_curations,'historical_path_aliases':aliases,'driver':driver,'history_roots':list(map(str,roots)),'known_ids':sorted(known_ids),'diagnostics':retained,'related_history':census,'physical_alias_groups':physical_aliases([original,tracker,*canonical_curations,driver,*retained,*census]),'historical_tracker_value':d,'pre_audit_boundary':d['first_blocker'],'model_history_route':'pre_audit_not_entered','local_attempt_history':'retained_without_reset_or_new_retry_allowance'}

def validate(proof,*,workbook,original,history_roots,known_ids):
    value=read(proof)
    if value.get('schema_version')in {'harbor-legacy-completed-research/v1','harbor-legacy-completed-research-build/v1','harbor-legacy-package-authoring/v1','harbor-legacy-staged-authoring/v1','harbor-legacy-opaque-authoring/v1'}:
        from scripts.batch import harbor_legacy_research_history as legacy
        return legacy.validate(proof,workbook=workbook,original=original,history_roots=history_roots,known_ids=known_ids)
    require(value.get('schema_version')==SCHEMA and value.get('workbook')==workbook and value.get('original')==original,'pre-audit proof identity differs')
    require(value.get('history_roots')==list(map(lambda x:str(Path(x).absolute()),history_roots)) and value.get('known_ids')==sorted(known_ids),'pre-audit proof namespace or inventory differs')
    expected=project(workbook=workbook,original=original,tracker=value['tracker'],curations=value['curations'],driver=value['driver'],history_roots=history_roots,known_ids=known_ids,historical_path_aliases=value.get('historical_path_aliases'))
    require(expected==value,'pre-audit proof failed current exact rederivation')
    return value
