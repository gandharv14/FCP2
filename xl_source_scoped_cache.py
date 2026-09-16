"""Exact selected-task cache proof; it makes no whole-workbook equivalence claim."""
from pathlib import Path
import json, math, tempfile, tomllib

SCHEMA = 'source-cache-selected-scope/v1'
POLICY = 'source-cache-equivalence-selected-scope/v4'


def selection(row, source):
    import xl_source_cache_equivalence as C
    s = C.read(row)
    C.require(set(s) == {'schema_version', 'source', 'projected_sha256',
        'original_curation', 'curation', 'ordered_outputs', 'excluded_bands',
        'prior_failure', 'diagnostic'}, 'closed selected-source scope required')
    C.require(s['schema_version'] == SCHEMA and s['source'] == C.binding(source),
        'selected scope original source differs')
    before = tomllib.loads(_text(s['original_curation']))
    after = tomllib.loads(_text(s['curation']))
    excluded = s['excluded_bands']
    C.require(isinstance(excluded, list) and excluded and len(excluded) == len(set(excluded)),
        'exact excluded whole bands required')
    expected = json.loads(json.dumps(before)); found = set()
    for r in expected['output']:
        if r['band'] in excluded:
            C.require(r['include'] is True, 'excluded band was not originally selected')
            r['include'] = False; found.add(r['band'])
    C.require(found == set(excluded) and expected == after, 'scope changed more than whole-band includes')
    outputs = s['ordered_outputs']
    C.require(isinstance(outputs, list) and 0 < len(outputs) <= 100
        and all(isinstance(x, str) for x in outputs) and len(outputs) == len(set(outputs)),
        'bounded distinct selected outputs required')
    prior = C.read(s['prior_failure'])
    C.require(prior['models'] == prior['claims'] == 0 and prior['result']['state'] == 'STOPPED'
        and prior['result']['bindings']['source_sha256'] == s['source']['sha256']
        and prior['result']['stage'] == 'source_cache_equivalence', 'original whole-source failure differs')
    diagnostic = C.read(s['diagnostic']); v = diagnostic['verification']
    C.require(v['passed'] is True and v['status'] == 'pass' and v['blocking_reasons'] == []
        and v['ordered_output_cells'] == outputs
        and v['outputs'] == dict(match=len(outputs), mismatch=0, unresolved=0, unverifiable=0),
        'original scoped output diagnostic differs')
    C.require(v['fingerprints']['curation']['sha256'] == s['curation']['sha256'],
        'diagnostic curation differs')
    return s


def _text(row):
    import xl_source_cache_equivalence as C
    C.require(set(row) == {'path', 'sha256'} and C.binding(row['path']) == row,
        'scoped curation bytes changed')
    return Path(row['path']).read_text()


def prove(source, s, maximum, tolerance):
    """Recompute and compare every formula in the exact active selected cones."""
    import xl_source_cache_equivalence as C
    from xl_source_health import inspect_workbook
    from xl_ast_graph import AstGraph, write_csv
    from xl_seg import model, project, evaluate, bands, condense, frontier, emit
    from xl_segment import stabilize_runtime_proof
    table_projection = s.get('schema_version') == 'presentation-table-selected-cache-proof/v1'
    if table_projection:
        from xl_table_scoped_cache import validate_scope
        validate_scope(source, s)
    before = C.digest(source); health = inspect_workbook(source)
    C.require(before == s['projected_sha256'] and health['route'] == 'recalc_candidate'
        and set(health['reason_codes']) == {'manual_calculation_mode'}
        and 0 < health['formula_count'] <= maximum, 'scoped projection or source health differs')
    with tempfile.TemporaryDirectory(prefix='source-selected-proof-') as tmp:
        ast = Path(tmp)
        with AstGraph(source, max_range_expand=100, read_values=True, consts=True, verbose=False) as tree:
            tree.build(); write_csv(ast, tree.nodes, tree.edges)
        graph = model.load(ast, Path(source).stem)
        try:
            cg = project.build(graph); bg = bands.build(cg)
            bands.attach_literal_sources(bg, cg, frontier.is_notable_literal); cd = condense.build(bg)
            emit.write_curation(ast, Path(source).stem, frontier.score_outputs(bg, cd), 6., 40)
            if not table_projection:
                C.require(tomllib.loads((ast/'curation.toml').read_text()) == tomllib.loads(_text(s['original_curation'])),
                    'original source candidate bands changed')
            entries = tomllib.loads(_text(s['curation']))['output']
            targets = set()
            for entry in entries:
                if entry['include']:
                    C.require(entry['band'] in bg.bands, 'selected source band absent')
                    targets.update(bg.bands[entry['band']].cells)
            C.require(targets == set(s['ordered_outputs']), 'selected source output cells differ')
            result, inputs, radj, proof = stabilize_runtime_proof(graph, cg, set(), targets,
                calculation=evaluate.workbook_calculation_metadata(source))
            cone = set(targets); pending = list(targets)
            while pending:
                for dep in radj.get(pending.pop(), ()):
                    if dep not in cone: cone.add(dep); pending.append(dep)
            formulas = {c for c in cone if c in cg.info and cg.info[c].node.kind == 'formula'}
            C.require(0 < len(formulas) <= 10000 and formulas <= result.evaluated_cells and result.strict_proof
                and not (inputs & formulas) and not (result.seeded_cells & formulas)
                and not result.oracle_reads and not result.oracle_accesses
                and result.coverage.get('expected_cache_reads') == 0,
                'selected formula proof used seeds/cache or omitted a dependency')
            C.require(proof['closure']['stabilized'] and proof['closure']['targets_stable']
                and not result.iterated and not result.cycle_groups
                and result.coverage.get('active_cycles') == 0,
                'selected dependency cones contain cycles or unstable targets')
            C.require(result.coverage.get('errors') == result.coverage.get('unresolved') == 0
                and not result.coverage.get('unknown_ops'), 'selected formula computation incomplete')
            cache = evaluate.workbook_expected_cache(source); C.require(cache is not None, 'source cache absent')
            comparisons = []
            try:
                for cell in sorted(formulas):
                    sheet, row, col = model.split_ref(cell); expected = cache(sheet, row, col)
                    actual = result.values.get(cell, evaluate.Unresolved('missing_formula_result'))
                    verdict, _ = evaluate.compare(expected, actual, tolerance=tolerance)
                    C.require(not isinstance(actual, (evaluate.ExcelError, evaluate.Unresolved)) and verdict == 'match',
                        'selected cached formula mismatch: ' + cell)
                    C.require(isinstance(expected, bool) == isinstance(actual, bool), 'selected Boolean type differs: ' + cell)
                    if isinstance(expected, str) or isinstance(actual, str):
                        C.require(expected == actual, 'selected string value differs: ' + cell)
                    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                        C.require(math.isfinite(actual), 'selected nonfinite formula: ' + cell)
                    comparisons.append(dict(cell=cell, expected=repr(expected), actual=repr(actual)))
            finally: cache.close()
            C.require(C.digest(source) == before, 'source changed during scoped proof')
            return dict(scope_kind='selected_dependency_cones', ordered_outputs=s['ordered_outputs'],
                curation=s['curation'], selected_formulas=len(formulas),
                formula_set_sha256=C.object_hash(sorted(formulas)), comparisons_sha256=C.object_hash(comparisons),
                primitive_inputs_sha256=C.object_hash(sorted(inputs)), cone_sha256=C.object_hash(sorted(cone)),
                formula_seeds=0, oracle_reads=0, active_cycles=0, all_selected_formula_cells_match=True,
                whole_workbook_equivalence=False, tolerance=tolerance)
        finally: graph.close()


def check_generation(manifest, curation, verification):
    import xl_source_cache_equivalence as C
    p = C.read(manifest['policy']); s = C.read(p['scope'])
    C.require(C.binding(curation)['sha256'] == s['curation']['sha256']
        and verification['ordered_output_cells'] == s['ordered_outputs'],
        'production generation changed the authorized selected scope')


def required_curation(policy_row, curation):
    import xl_source_cache_equivalence as C
    p = C.read(policy_row); s = C.read(p['scope'])
    C.require(C.binding(curation)['sha256'] == s['curation']['sha256'],
        'source build must select the authorized scoped curation')
    return s
