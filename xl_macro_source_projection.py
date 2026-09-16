"""Explicit VBA-only projection with full primitive formula recomputation.

Original bytes remain evidence. No macro is executed, no calculated cache is an
input, and the derived workbook still requires ordinary source/task validation.
Active controls, signatures, embeddings and other source barriers fail closed.
"""
from pathlib import Path
import hashlib, math, posixpath, tempfile, time, zipfile
from xml.etree import ElementTree as ET
from xl_source_cache_equivalence import (AUTHORIZATION_SHA256, binding, digest,
    keep, object_hash, package, read, require)

POLICY = 'source-vba-only-projection/v1'
SCHEMA = 'source-vba-only-projection-manifest/v1'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
VBA_REL = 'http://schemas.microsoft.com/office/2006/relationships/vbaProject'
VBA_TYPE = 'application/vnd.ms-office.vbaProject'
MACRO_TYPE = 'application/vnd.ms-excel.sheet.macroEnabled.main+xml'
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'


def implementation():
    # Inventory approval is a downstream decision over this proof, so including
    # its registry in this proof's hash would create a circular hash dependency.
    # Every executable source dependency remains authenticated. The original
    # inventory gate independently validates its own pinned registry bytes.
    from scripts.corpus import code_files
    records=code_files()
    return object_hash({k:v for k,v in records.items()
        if k!='verification_manifests/approved_source_inventories.v2.json'})


def policy(row, source, *, at=None):
    p = read(row)
    require(set(p) == {'schema_version', 'workbook', 'source_sha256', 'authorization',
        'created_at_ns', 'expires_at_ns', 'max_formula_cells', 'relative_tolerance'}
        and p['schema_version'] == POLICY, 'closed VBA-only policy required')
    require(Path(source).suffix == '.xlsm' and p['workbook'] == Path(source).stem
        and p['source_sha256'] == digest(source), 'exact original XLSM required')
    require(binding(p['authorization']['path']) == p['authorization']
        and p['authorization']['sha256'] == AUTHORIZATION_SHA256, 'production authorization differs')
    require(type(p['created_at_ns']) is int and type(p['expires_at_ns']) is int
        and p['created_at_ns'] < p['expires_at_ns'] <= p['created_at_ns'] + 24*3600*10**9
        and p['created_at_ns'] <= (time.time_ns() if at is None else at) < p['expires_at_ns'],
        'bounded live projection policy required')
    require(type(p['max_formula_cells']) is int and 0 < p['max_formula_cells'] <= 10000
        and type(p['relative_tolerance']) is float and p['relative_tolerance'] == 1e-6,
        'bounded complete formula policy required')
    return p


def projected_members(members):
    """Remove exactly one VBA part/relation/type; preserve every other payload."""
    forbidden = ('activex', 'ctrlprops', 'customui', 'macrosheets', 'dialogsheets',
                 'embeddings', 'vbaprojectsignature', '_xmlsignatures')
    require(not any(any(x in name.lower() for x in forbidden) for name in members),
            'active controls, embeddings or signatures require another policy')
    require('xl/vbaProject.bin' in members and members['xl/vbaProject.bin'], 'VBA payload absent')
    for name, raw in members.items():
        if name.endswith('.rels'):
            root = ET.fromstring(raw)
            require(root.tag == '{'+REL+'}Relationships', 'unknown relationship schema')
            for item in root:
                require(item.tag == '{'+REL+'}Relationship' and item.get('TargetMode') != 'External',
                        'external or unknown relationship forbidden')
                kind = item.get('Type', '')
                require(not any(x in kind.lower() for x in ('control', 'oleobject', 'attachedtemplate', 'macrosheet')),
                        'active relationship forbidden')
                if 'vba' in kind.lower():
                    require(name == 'xl/_rels/workbook.xml.rels' and kind == VBA_REL,
                            'additional VBA relationship forbidden')
    types = ET.fromstring(members['[Content_Types].xml'])
    require(types.tag == '{'+CT+'}Types', 'unknown content types schema')
    workbook = [x for x in types if x.get('PartName') == '/xl/workbook.xml']
    vba = [x for x in types if x.get('PartName') == '/xl/vbaProject.bin']
    require(len(workbook) == len(vba) == 1 and workbook[0].get('ContentType') == MACRO_TYPE
        and vba[0].get('ContentType') == VBA_TYPE and vba[0].tag == '{'+CT+'}Override',
        'exact workbook and VBA content declarations required')
    require(not any('macroenabled' in x.get('ContentType', '').lower() or 'vba' in x.get('ContentType', '').lower()
        for x in types if x not in (workbook[0], vba[0])), 'additional executable content declaration')
    types.remove(vba[0]);workbook[0].set('ContentType', XLSX_TYPE)
    rels = ET.fromstring(members['xl/_rels/workbook.xml.rels'])
    links = [x for x in rels if x.get('Type') == VBA_REL]
    require(len(links) == 1 and links[0].get('TargetMode') is None
        and posixpath.normpath(posixpath.join('xl', links[0].get('Target', ''))) == 'xl/vbaProject.bin',
        'exact relative VBA relationship required')
    rels.remove(links[0])
    result = dict(members);del result['xl/vbaProject.bin']
    result['[Content_Types].xml'] = ET.tostring(types, encoding='utf-8', xml_declaration=True)
    result['xl/_rels/workbook.xml.rels'] = ET.tostring(rels, encoding='utf-8', xml_declaration=True)
    return result


def prove(source, maximum, tolerance):
    from xl_source_health import inspect_workbook
    from xl_ast_graph import AstGraph, write_csv
    from xl_seg import model, project, evaluate
    from xl_segment import stabilize_runtime_proof
    health = inspect_workbook(source)
    require(set(health['reason_codes']) == {'macros_present'}, 'additional source barrier forbidden')
    require(0 < health['formula_count'] <= maximum, 'complete formula scope exceeds policy')
    before = digest(source);projected_members(package(source)[1])
    with tempfile.TemporaryDirectory(prefix='vba-formula-proof-') as tmp:
        with AstGraph(source, max_range_expand=100, read_values=True, consts=True, verbose=False) as tree:
            tree.build();write_csv(Path(tmp), tree.nodes, tree.edges)
        graph = model.load(Path(tmp), Path(source).stem)
        try:
            cg = project.build(graph);formulas = {c for c,i in cg.info.items() if i.node.kind == 'formula'}
            require(len(formulas) == health['formula_count'], 'original formula coverage differs')
            result, inputs, _, closure = stabilize_runtime_proof(graph, cg, set(), formulas,
                calculation=evaluate.workbook_calculation_metadata(source))
            require(result.strict_proof and not(inputs & formulas) and not(result.seeded_cells & formulas)
                and not result.oracle_reads and not result.oracle_accesses
                and result.coverage.get('expected_cache_reads') == 0, 'formula cache or oracle input forbidden')
            require(formulas <= result.evaluated_cells and closure['closure']['stabilized']
                and closure['closure']['targets_stable'], 'complete primitive closure required')
            require(not result.iterated and not result.cycle_groups and result.coverage.get('active_cycles') == 0
                and result.coverage.get('errors') == result.coverage.get('unresolved') == 0
                and not result.coverage.get('unknown_ops'), 'cycles, errors, UDFs or unresolved formulas forbidden')
            # Expected values become accessible only after all formulas compute.
            cache = evaluate.workbook_expected_cache(source);require(cache is not None, 'expected cache absent');rows = []
            try:
                for cell in sorted(formulas):
                    sheet,row,col = model.split_ref(cell);expected = cache(sheet,row,col)
                    actual = result.values.get(cell, evaluate.Unresolved('missing'))
                    verdict,_ = evaluate.compare(expected,actual,tolerance=tolerance)
                    require(verdict == 'match' and not isinstance(actual,(evaluate.ExcelError,evaluate.Unresolved)),
                            'original formula cache mismatch: '+cell)
                    require(isinstance(expected,bool) == isinstance(actual,bool), 'Boolean type mismatch: '+cell)
                    if isinstance(expected,str) or isinstance(actual,str):require(expected == actual, 'string mismatch: '+cell)
                    if isinstance(actual,(int,float)) and not isinstance(actual,bool):require(math.isfinite(actual), 'nonfinite result')
                    rows.append(dict(cell=cell,expected=repr(expected),actual=repr(actual)))
            finally:cache.close()
            require(digest(source) == before, 'original source changed')
            return dict(formulas=len(formulas),formula_set_sha256=object_hash(sorted(formulas)),
                comparisons_sha256=object_hash(rows),primitive_inputs_sha256=object_hash(sorted(inputs)),
                formula_seeds=0,oracle_reads=0,active_cycles=0,closure_stabilized=True,
                all_formula_cells_match=True,tolerance=tolerance)
        finally:graph.close()


def create(source, directory, policy_binding):
    source=Path(source).absolute();p=policy(policy_binding,source);directory=Path(directory).absolute()
    require(not directory.exists(),'projection namespace consumed');directory.mkdir(parents=True)
    proof=keep(directory/'formula-proof.json',prove(source,p['max_formula_cells'],p['relative_tolerance']))
    infos,before=package(source);after=projected_members(before);candidate=directory/(source.stem+'.xlsx')
    with zipfile.ZipFile(candidate,'x') as z:
        for info in infos:
            if info.filename in after:z.writestr(info,after[info.filename])
    manifest=dict(schema_version=SCHEMA,policy_version=POLICY,policy=policy_binding,
        authorized_at_ns=time.time_ns(),implementation_sha256=implementation(),source=binding(source),
        candidate=binding(candidate),proof=proof,removed_part_sha256=hashlib.sha256(before['xl/vbaProject.bin']).hexdigest(),
        macros_executed=False,models_dispatched=0,source_qualified=False)
    keep(directory/'manifest.json',manifest);verify(source,candidate,manifest)
    return candidate,manifest


def verify(source,candidate,manifest):
    require(set(manifest)=={'schema_version','policy_version','policy','authorized_at_ns','implementation_sha256',
        'source','candidate','proof','removed_part_sha256','macros_executed','models_dispatched','source_qualified'}
        and manifest['schema_version']==SCHEMA and manifest['policy_version']==POLICY,'closed macro lineage required')
    if manifest['implementation_sha256']!=implementation():
        from xl_macro_source_history import verify as verify_original
        return verify_original(source,candidate,manifest)
    require(manifest['source']==binding(source) and manifest['candidate']['sha256']==digest(candidate)
        and binding(manifest['candidate']['path'])==manifest['candidate'], 'source/candidate bytes changed')
    require(manifest['implementation_sha256']==implementation() and manifest['macros_executed'] is False
        and manifest['models_dispatched']==0 and manifest['source_qualified'] is False,'projection claims changed')
    p=policy(manifest['policy'],source,at=manifest['authorized_at_ns']);before=package(source)[1]
    require(hashlib.sha256(before['xl/vbaProject.bin']).hexdigest()==manifest['removed_part_sha256']
        and package(candidate)[1]==projected_members(before),'formula, cache, layout or unrelated package bytes changed')
    require(prove(source,p['max_formula_cells'],p['relative_tolerance'])==read(manifest['proof']), 'fresh full formula proof differs')
    from xl_source_health import inspect_workbook
    health=inspect_workbook(candidate)
    require(health['route']=='pass' or (health['route']=='restricted_pass'
        and set(health['reason_codes'])=={'worksheet_offset'}), 'projected source still has an unsupported barrier')
    return dict(original_sha256=digest(source),candidate_sha256=digest(candidate),macros_executed=False,
        source_qualified=False,source_route=health['route'],requires_approved_inventory=health['route']=='restricted_pass')
