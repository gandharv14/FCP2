"""Exact normalization applicability across proof-code/receipt refresh only.

Original raw requests/responses and financial evidence remain unchanged. Current
source/audit applicability is revalidated independently; historical code is read
only to bind provenance and is never executed.
"""
from dataclasses import asdict
from pathlib import Path
import copy
import json

import xl_audit_equivalence as eq
import xl_audit_validator_upgrade as upgrade
from scripts.qualified_publication import validate_audit_bindings
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_review_context import normalization_review,json_value

SCHEMA='normalization-audit-applicability/v1'


def locator_continuity(previous, current, *, bindings, original_locator_code):
    """Caller also validates the current receipt through the owning validator."""
    old=read(previous);new=read(current);upgrade.checked(previous);upgrade.checked(current)
    code=upgrade.checked(original_locator_code)
    require(old.get('schema_version')==eq.SCHEMA==new.get('schema_version'),'unsupported locator receipt schema')
    require(old.get('adapter_sha256')==upgrade.digest(code),'original locator generator identity changed')
    require(old.get('auditor_sha256')==upgrade.ORIGINAL_SHA256,'unsupported original auditor')
    require(new.get('adapter_sha256')==eq.digest(eq.__file__) and new.get('auditor_sha256')==eq.digest(eq.audit.__file__),
            'current locator code identity changed')
    require(set(new)==set(old)|{'validator_upgrade','original_auditor'},'locator receipt shape changed outside proof refresh')
    require(new['validator_upgrade']==bindings['audit_validator_upgrade'] and new['original_auditor']==bindings['audit_upgrade_original_code'],
            'current audit upgrade selection changed')
    except_keys={'adapter_sha256','auditor_sha256','checked_files','validator_upgrade','original_auditor'}
    require({k:v for k,v in old.items() if k not in except_keys}=={k:v for k,v in new.items() if k not in except_keys},
            'locator financial context/targets/history changed')
    proof=read(bindings['audit_validator_upgrade'])
    # These three extra files are exactly the new proof artifacts. Existing
    # inventory, request, policy, source and verifier files may not disappear.
    extra={proof['identity']['original_generator']['path'],proof['identity']['adapter']['path'],bindings['audit_validator_upgrade']['path']}
    if proof['identity'].get('inventory_lookup_code') is not None:
        from xl_audit_inventory_upgrade import evidence
        lookup=evidence()
        require(proof['identity']['inventory_lookup_code']==lookup,'unreviewed inventory lookup proof')
        extra.add(lookup['path'])
    old_root=Path(eq.audit.__file__).resolve().parent
    old_codes={str(old_root/'xl_audit_equivalence.py'):old['adapter_sha256'],
               str(old_root/'xl_variable_source_audit.py'):old['auditor_sha256']}
    new_codes={str(Path(eq.__file__).resolve()):new['adapter_sha256'],
               str(Path(eq.audit.__file__).resolve()):new['auditor_sha256']}
    old_files=dict(old['checked_files']);new_files=dict(new['checked_files'])
    for path,digest in old_codes.items():require(old_files.pop(path,None)==digest,'original locator code evidence missing')
    for path,digest in new_codes.items():require(new_files.pop(path,None)==digest,'current locator code evidence missing')
    additions=set(new_files)-set(old_files)
    require(additions==extra-set(old_files),'new locator proof evidence differs outside exact upgrade artifacts')
    require(set(old_files)<=set(new_files),'old locator evidence was dropped')
    for path,digest in old_files.items():
        require(new_files[path]==digest,'locator evidence changed outside code identity')
        upgrade.checked({'path':path,'sha256':digest})
    for path in additions:upgrade.checked({'path':path,'sha256':new_files[path]})
    return {'previous':previous,'current':current,'original_locator_code':original_locator_code,
            'unchanged_financial_evidence':dict(sorted(old_files.items())),
            'added_upgrade_evidence':{p:new_files[p] for p in sorted(additions)},'historical_code_executed':False}


def draft_relocation(body,manifest,*,source,baseline,draft,rows):
    """Independently reanalyse an exact draft copy; allow only its evidence path."""
    previous=body['draft']
    upgrade.checked(previous);upgrade.checked(draft)
    require(previous['sha256']==draft['sha256'] and Path(previous['path']).read_bytes()==Path(draft['path']).read_bytes(),
            'normalization draft content changed')
    if previous['path']==draft['path']:
        return rows, None
    import gen_normalizer as gen
    original=Path(previous['path']);current=Path(draft['path']);workbook=Path(source['path']).stem
    require(original.name==current.name=='draft.json' and original.parent.name==current.parent.name==workbook+'-variable-sources',
            'draft relocation is not between canonical workbook runs')
    old_draft,old_rows=gen.analyse(workbook,source['path'],source['sha256'],
        runs_root=original.parent.parent,inputs_root=Path(baseline['path']).parent)
    require(old_draft==read(draft),'retained draft parse changed')
    require(json_value(old_rows)==read(manifest['analysis']),'original analysis no longer reconstructs')
    projected=copy.deepcopy(rows)
    for row in projected:
        require(row['evidence']['draft_sha256']==draft['sha256'] and row['evidence_paths']['draft_sha256']==str(current),
                'current row draft evidence differs')
        row['evidence_paths']['draft_sha256']=str(original)
    require(json_value(projected)==json_value(old_rows),'normalization changed beyond exact draft evidence path')
    return old_rows, {'original':previous,'current':draft,'same_bytes':True,
        'changed_field':'rows[*].evidence_paths.draft_sha256','current_generator':record(Path(gen.__file__))}


def propose_applicability(prepared,call,*,source,baseline,draft,rows,bindings,policy,original_locator_code):
    # Current owning gate is mandatory before historical request interpretation.
    files=validate_audit_bindings(bindings,expected_generations={k:bindings[k+'_generation']['id'] for k in ('source','segmentation')})
    manifest=read(prepared);body=read(manifest['evidence'])
    previous=body.get('audit_equivalence');current=bindings.get('audit_equivalence')
    require(previous is not None and current is not None and previous!=current,'explicit changed proof locator required')
    continuity=locator_continuity(previous,current,bindings=bindings,original_locator_code=original_locator_code)
    old_bindings=copy.deepcopy(bindings);old_bindings['audit_equivalence']=previous
    historical_rows,relocation=draft_relocation(body,manifest,source=source,baseline=baseline,draft=draft,rows=rows)
    # Exactly original bytes, current source/baseline/draft/rows and primitive
    # context. Only the historical audit-proof record is selected separately.
    reviewed=normalization_review(call,prepared,source=source,baseline=baseline,draft=body['draft'],
        rows=historical_rows,bindings=old_bindings,policy=policy)
    # Repeat the body check with a derived in-memory proof locator to demonstrate
    # there is no other semantic/context change. Never save this as model input.
    from scripts.batch.harbor_review_context import normalization_body
    projected=copy.deepcopy(body);projected['audit_equivalence']=current
    projected['draft']=draft;projected['candidates']=json_value([r for r in rows if r['clean']])
    normalization_body(projected,source=source,baseline=baseline,draft=draft,rows=rows,bindings=bindings)
    bound={'prepared':prepared,'call':asdict(call),'source':source,'baseline':baseline,'draft':draft,
        'analysis':manifest['analysis'],'evidence':manifest['evidence'],'policy':asdict(policy)}
    for item in (prepared,*asdict(call).values(),source,baseline,draft,body['draft'],manifest['analysis'],manifest['evidence'],original_locator_code):
        if item is not None:
            p=upgrade.checked(item);files[str(p.resolve())]=upgrade.digest(p)
    if relocation is not None:
        files[relocation['current_generator']['path']]=relocation['current_generator']['sha256']
    files[previous['path']]=previous['sha256']
    files[str(Path(__file__).resolve())]=upgrade.digest(__file__)
    return {'schema_version':SCHEMA,'bound':bound,'current_bindings':eq.selected(bindings),
        'continuity':continuity,'draft_relocation':relocation,'model_response_reused_exactly':True,'semantic_attempts_added':0,
        'semantic_budget_reset':False,'lane_approval':False,'checked_files':dict(sorted(files.items()))}


def validate_applicability(receipt,prepared,call,*,source,baseline,draft,rows,bindings,policy,original_locator_code):
    upgrade.checked(receipt)
    from scripts.batch.harbor_normalization_inventory_applicability import SCHEMA as INVENTORY_SCHEMA, validate
    if read(receipt).get('schema_version')==INVENTORY_SCHEMA:
        return validate(receipt,prepared,call,source=source,baseline=baseline,draft=draft,rows=rows,
            bindings=bindings,policy=policy,original_locator_code=original_locator_code)
    expected=propose_applicability(prepared,call,source=source,baseline=baseline,draft=draft,rows=rows,
        bindings=bindings,policy=policy,original_locator_code=original_locator_code)
    require(read(receipt)==expected,'normalization audit applicability is stale or altered')
    # Return the exact independently validated review to the original owning
    # normalization gate, which still validates current emission and plain rules.
    return {'review':read(call.parsed),'checked_files':{**expected['checked_files'],receipt['path']:receipt['sha256']}}
