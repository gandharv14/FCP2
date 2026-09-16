"""Explicitly redact only original data-table results outside every graded cone.

Original bytes remain intact. This does not constitute recalculation or task
approval: original and candidate numerical gates both remain mandatory.
"""
from pathlib import Path
import json,re,shutil
from argparse import Namespace
from xl_presentation_tables import (table_inventory,check_exclusion,scan_leaks,
    _package,_cell_blocks,_attrs,FORMULA_TAG_RE,_write_package,sha256_file,
    canonical_bytes,sha256_bytes)
from scripts.batch.hardened_runner import atomic_write_json
POLICY='out-of-cone-presentation-redaction/v1'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular redaction evidence required')
    return dict(path=str(p),sha256=sha256_file(p))


def selected(row,source):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'redaction policy changed')
    p=json.loads(Path(row['path']).read_bytes());require(set(p)=={'schema_version','source','authorization','tables'} and p['schema_version']==POLICY,'closed redaction policy required')
    require(p['source']==binding(source),'redaction source changed')
    require(p['authorization']==binding(p['authorization']['path']) and p['authorization']['sha256']==AUTHORIZATION_SHA256,'redaction authorization differs')
    tables=table_inventory(_package(source)[1]);cells=[(t['part'],c)for t in tables for c in t['cells']]
    require(p['tables']==tables and len(cells)<=100000 and len(cells)==len(set(cells)),'exact bounded nonoverlapping original table inventory required')
    return p,tables


def transformed_members(members,tables):
    """Retain all bytes except values/formulas inside selected result rectangles."""
    result=dict(members);selected_cells={}
    for t in tables:
        for cell in t['cells']:selected_cells.setdefault(t['part'],set()).add(cell.rsplit('!',1)[1])
    for part,cells in selected_cells.items():
        text=members[part].decode('utf-8');blocks=_cell_blocks(text)
        for cell in sorted(cells):
            if cell not in blocks:continue # An absent original result stays absent.
            block=blocks[cell]['block'];formulas=list(FORMULA_TAG_RE.finditer(block))
            require(all(_attrs(m.group('attrs')or m.group('self_attrs')or'').get('t')=='dataTable'for m in formulas),'ordinary formula occurs inside declared table results')
            require(text.count(block)==1,'ambiguous table cell XML')
            opening=re.match(r'<c\b([^>]*)>',block)
            require(opening is not None,'unsupported table cell XML')
            if opening[1].rstrip().endswith('/'):
                continue # An explicitly empty original result stays byte-identical.
            attrs=re.sub(r'\s+t\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27)','',opening[1])
            text=text.replace(block,'<c'+attrs+'/>',1)
        result[part]=text.encode('utf-8')
    return result


def inspect_original(source,workbook,curation,root):
    from xl_ast_graph import main as ast_main
    from xl_segment import segment
    from xl_seg.publication import validate_generation_directory
    from xl_source_publication import create_ast_provenance
    private=root/'inspection-source';private.mkdir();inspected=private/(workbook+'.xlsx');shutil.copyfile(source,inspected)
    ast_main([str(inspected),'-o',str(root/'ast'),'--production','--quiet'])
    provenance=create_ast_provenance(inspected,root/'ast'/workbook,builder_args=['--production','--quiet'])
    segment_root=root/'inspection-segments'/workbook;segment_root.mkdir(parents=True)
    if curation is not None:shutil.copyfile(curation,segment_root/'curation.toml')
    options=Namespace(ast_dir=str(root/'ast'),source=str(private),out=str(segment_root.parent),allow_legacy_ast=True,threshold=6.,top=40,sample=400,lineage_max=4000,recurate=False,llm=False,no_auto_adjudication=True,model='',env_file='',no_verify=False)
    payload=segment(workbook,options);generation=Path(payload['generation']['directory']);validate_generation_directory(generation,require_pass=True)
    if curation is not None:require(Path(curation).read_bytes()==(segment_root/'curation.toml').read_bytes(),'curation changed during original inspection')
    return payload,generation,provenance,segment_root/'curation.toml'


def create(source,workbook,directory,policy,*,curation=None):
    source=Path(source).absolute();root=Path(directory).absolute();p,tables=selected(policy,source);require(source.stem==workbook and not root.exists(),'new exact redaction namespace required');root.mkdir(parents=True)
    payload,generation,provenance,curation=inspect_original(source,workbook,curation,root)
    outputs,cone=check_exclusion(tables,payload)
    infos,members=_package(source);candidate=root/(workbook+'.xlsx');_write_package(infos,transformed_members(members,tables),candidate)
    scan_leaks(candidate,tables,outputs)
    from xl_source_health import inspect_workbook
    health=inspect_workbook(candidate);require(health['route']=='pass','redacted candidate still has a source barrier')
    receipt=dict(schema_version=POLICY,policy_version=POLICY,policy=policy,source=binding(source),candidate=binding(candidate),curation=binding(curation),inspection_generation=str(generation),inspection_manifest=binding(generation/'generation-manifest.json'),inspection_ast_provenance=provenance,tables=tables,ordered_outputs=outputs,excluded_cone_sha256=sha256_bytes(canonical_bytes(cone)),redacted_cells=sum(len(t['cells'])for t in tables),recalculation_performed=False,task_approved=False)
    receipt['manifest_sha256']=sha256_bytes(canonical_bytes(receipt));atomic_write_json(root/'manifest.json',receipt);verify(source,candidate,receipt);return candidate,receipt


def verify(source,candidate,receipt):
    from xl_seg.publication import validate_generation_directory,resolve_generation_by_id
    from xl_source_publication import validate_ast_provenance,_default_builder_paths
    fields={'schema_version','policy_version','policy','source','candidate','curation','inspection_generation','inspection_manifest','inspection_ast_provenance','tables','ordered_outputs','excluded_cone_sha256','redacted_cells','recalculation_performed','task_approved','manifest_sha256'}
    require(set(receipt)==fields and receipt['schema_version']==receipt['policy_version']==POLICY,'closed redaction receipt required')
    require(receipt['manifest_sha256']==sha256_bytes(canonical_bytes({k:v for k,v in receipt.items()if k!='manifest_sha256'})),'redaction receipt changed')
    p,tables=selected(receipt['policy'],source);require(receipt['source']==binding(source) and receipt['candidate']==binding(candidate) and receipt['curation']==binding(receipt['curation']['path']),'redaction custody differs')
    generation=Path(receipt['inspection_generation']);require(binding(generation/'generation-manifest.json')==receipt['inspection_manifest'],'inspection generation changed');validate_generation_directory(generation,require_pass=True)
    original=generation.parents[3]/'inspection-source'/Path(source).name;require(sha256_file(original)==sha256_file(source),'inspection is not exact original');ast=generation.parents[3]/'ast'/Path(source).stem
    validate_ast_provenance(original,ast,receipt['inspection_ast_provenance'],builder_code_paths=_default_builder_paths())
    resolve_generation_by_id(generation.parents[1],generation.name,source_path=original,ast_dir=ast,require_pass=True,validate_live_evidence=True)
    payload=json.loads((generation/'segments.json').read_bytes());outputs,cone=check_exclusion(tables,payload)
    require(receipt['tables']==tables and receipt['ordered_outputs']==outputs and receipt['excluded_cone_sha256']==sha256_bytes(canonical_bytes(cone)) and receipt['redacted_cells']==sum(len(t['cells'])for t in tables),'graded cone or table scope changed')
    require(receipt['recalculation_performed']is False and receipt['task_approved']is False,'redaction claims changed')
    require(_package(candidate)[1]==transformed_members(_package(source)[1],tables),'candidate has edits outside approved table results');scan_leaks(candidate,tables,outputs)
    from xl_source_health import inspect_workbook
    require(inspect_workbook(candidate)['route']=='pass','redaction source still blocked')
    return dict(status='VERIFIED_REDACTION',source_qualified=False,task_approved=False)
