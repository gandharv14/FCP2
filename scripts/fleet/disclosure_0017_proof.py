"""Exact 0017 cumulative-ratio source proof and immutable continuation identity."""
from __future__ import annotations
import hashlib,json,copy,math
from pathlib import Path
SOURCE_SHA='d54f4c8b06b9655ab8922e8304bbb77036e37b7849c09206fc3314671b77cbf2'
ENTRY='method_cumulative_ratio'
SCHEMA='harbor-0017-cumulative-ratio-proof/v1'
PROFILE=None

def source_proof(renderer,bindings):
    require(set(bindings)=={'source','disclosure','records','faithcheck','delivered','answer_key'},'0017 closed source evidence required')
    for b in bindings.values():require(record(b['path'])==b,'0017 source evidence changed')
    require(bindings['source']['sha256']==SOURCE_SHA,'0017 exact original workbook required')
    original=read(bindings['disclosure']);records=read(bindings['records']);faith=read(bindings['faithcheck'])
    require(original['records']==records['records'] and original['method_assessments']==records['method_assessments'],'0017 detector/writer history differs')
    require(len(original['agent_records'])==46 and len(faith['faults'])==2 and all(x['kind']=='scope' and x['entry']=='method_debt_movement' for x in faith['faults']),'0017 known two-scope-fault boundary required')
    gold=renderer.Book(Path(bindings['source']['path']));delivered=renderer.Book(Path(bindings['delivered']['path']))
    require(gold.value.get('S&U!I7')=='Uses of Capital' and gold.value.get('S&U!M7')=='’12A Mult.' and gold.row_label('Financial_Model!N51')=='Management Adjusted EBITDA','0017 cumulative-ratio source headings changed')
    key=read(bindings['answer_key']);targets=key['targets'] if 'targets' in key else key
    # Use the owning answer-key decoder, with the exact retained key path.
    task=Path(bindings['answer_key']['path']).parent.parent
    targets,_=renderer.load_key(task);closure=renderer.graded_closure(gold,{renderer.parse_ref(x,gold.sheets[0]) for x in targets})
    rows=[]
    for n,label in ((8,'Purchase of Equity'),(9,'Refinanced Debt'),(10,'Transaction Expenses')):
        cell=f'S&U!M{n}';formula=f'=+SUM(K$8:K{n})/Financial_Model!$N$51'
        require(gold.formula.get(cell)==formula and gold.row_label(cell)==label and not delivered.has(cell),'0017 source mechanics/row label/delivered blank changed')
        numerator=sum(gold.value[f'S&U!K{k}'] for k in range(8,n+1));denominator=gold.value['Financial_Model!N51'];value=numerator/denominator
        require(math.isclose(value,gold.value[cell],rel_tol=1e-12,abs_tol=1e-12),'0017 independent numeric ratio differs')
        rows.append(dict(cell=cell,label=label,formula=formula,independent_value=value,cached_value=gold.value[cell],graded=cell in closure))
    require(gold.formula['S&U!M13']=='=+M10' and {'S&U!M10','S&U!M13'}<=closure,'0017 original graded alias differs')
    require(renderer.full_copied_scope_vertical(gold,['S&U!M9'],closure)==['S&U!M8','S&U!M9'],'0017 complete non-graded family changed')
    m9=[r for r in original['records'] if r.get('cell_keys')==['S&U!M9']]
    require(len(m9)==1 and m9[0]['entry']=='method_debt_movement' and m9[0]['disposition']=='disclosed' and m9[0]['coverage_complete'],'0017 original M9 detector differs')
    require(not any('S&U!M8' in r.get('cell_keys',[]) for r in original['records']),'0017 M8 already represented')
    require(next(r for r in original['records'] if r.get('cell_keys')==['S&U!M10'])['disposition']=='suppressed','0017 graded M10 suppression changed')
    return dict(schema_version=SCHEMA,bindings=bindings,rows=rows,non_graded_family=['S&U!M8','S&U!M9'],graded_closure=sorted(closure),original_record_count=len(original['records']),original_disclosed_count=46,corrected_disclosed_count=47,original_m9=m9[0],retained_assessments=original['method_assessments'],classification='Source-defined cumulative uses-of-capital to EBITDA ratio; no debt draw or repayment assertion.',review_ordinal=1,review_limit=1,model_calls=0,qualified=False)

def corrected_records(renderer,proof):
    """Return a new detector payload; never write or relax owning validators."""
    require(source_proof(renderer,proof['bindings'])==proof,'0017 full source proof changed')
    gold=renderer.Book(Path(proof['bindings']['source']['path']));old=read(proof['bindings']['records']);result=copy.deepcopy(old)
    require(ENTRY in renderer.METHOD_ENTRY_IDS and ENTRY in renderer.registry(),'0017 reviewed honest ratio registry entry required')
    replacement=[]
    for row in proof['rows'][:2]:
        cell=row['cell'];band=dict(band=renderer.pretty(cell),cell_keys=[cell],label=row['label'],pattern='')
        profile=renderer.formula_profile(gold,band);require(profile['complete'] and profile['formula']==row['formula'],'0017 exact ratio profile incomplete')
        fields=renderer.custom_sentence_fields(profile,gold);complete=fields.pop('_coverage_complete');require(complete,'0017 operand/lock coverage incomplete')
        item=renderer.record(ENTRY,'out_of_catalogue',[cell],row['formula'],renderer.registry()[ENTRY]['alternatives'],'Source-defined cumulative uses-of-capital ratio on row '+repr(row['label'])+'.',fields=fields)
        item.update(disposition='disclosed',source='custom_method_detector',custom_reason='source_proved_cumulative_uses_ratio',coverage_complete=True,method_profile={k:v for k,v in profile.items() if k!='ast'})
        replacement.append(item)
    index=next(i for i,r in enumerate(result['records']) if r==proof['original_m9'])
    result['records'][index:index+1]=replacement
    require(result['records'][:index]+result['records'][index+2:]==old['records'][:index]+old['records'][index+1:],'0017 unrelated record changed')
    require(len([r for r in result['records'] if r.get('disposition')=='disclosed'])==47,'0017 repaired record coverage changed')
    require(renderer._heterogeneous_scope_partitioned(gold,proof['non_graded_family'],replacement,ENTRY),'0017 unchanged owning full-partition check failed')
    # Historical neighbor-based debt-role assessments remain verbatim below.
    # The prospective current assessment must describe the actual ratio.
    for row in result['method_assessments']:
        if row.get('cell_keys') in [['S&U!M8'],['S&U!M9']]:
            row.update(roles=[ENTRY],entry=ENTRY,status='out_of_catalogue',reason='source_proved_cumulative_uses_ratio',role_context=['Uses of Capital','’12A Mult.','Management Adjusted EBITDA'])
    result['historical_scope_correction']=dict(schema_version=SCHEMA,source_proof=proof,retained_original_m9=proof['original_m9'],retained_original_assessments=old['method_assessments'],replacement_cells=proof['non_graded_family'],semantic_budget_reset=False,independent_review_required=True)
    return result

def require(ok,message):
    if not ok:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def record(p):return {'path':str(Path(p).absolute()),'sha256':sha(Path(p).read_bytes())}


def read(b):
    require(record(b['path'])==b,'0017 bound evidence changed');return json.loads(Path(b['path']).read_bytes())


def _text(b):
    require(record(b['path'])==b,'0017 bound text changed');return Path(b['path']).read_text()


def profile():
    require(PROFILE is not None,'0017 immutable profile is not configured');return read(PROFILE)


def verified_code(root):
    p=profile();old=read(p['previous']);expected={r['relative']:r['sha256'] for r in old['files']}
    for name,h in expected.items():require(sha((Path(old['root'])/name).read_bytes())==h,'0017 predecessor code changed')
    historical={str(f.relative_to(old['root'])):sha(f.read_bytes()) for f in Path(old['root']).rglob('*') if f.is_file() and '__pycache__' not in f.parts}
    require(historical==expected,'0017 full historical inventory changed')
    require(p['semantic_budget_reset'] is False and len(p['legacy_calls'])==3,'0017 successor scope differs')
    for name,change in p['patches'].items():
        if name in expected:require(change['before']==record(Path(old['root'])/name),'0017 patch predecessor differs')
        else:require(change['before'] is None,'0017 unexpected added-file predecessor')
        raw=Path(change['after']['path']).read_bytes();require(sha(raw)==change['after']['sha256'],'0017 exact patch witness changed');expected[name]=sha(raw)
    template=_text(p['proof_template']);needle='\nPROFILE=None\n';require(template.count(needle)==1,'0017 proof template ambiguous')
    expected['scripts/fleet/disclosure_0017_proof.py']=sha(template.replace(needle,'\nPROFILE='+repr(PROFILE)+'\n',1).encode())
    expected.pop('.pipeline-code.json',None)
    actual={str(f.relative_to(root)):sha(f.read_bytes()) for f in Path(root).rglob('*') if f.is_file() and '__pycache__' not in f.parts}
    actual.pop('.pipeline-code.json',None)
    require(read(record(Path(root)/'.pipeline-code.json'))==actual,'0017 successor manifest changed')
    require(actual==expected,'0017 unreviewed successor code delta')
    return sorted(name for name in actual if actual[name]!={r['relative']:r['sha256'] for r in old['files']}.get(name))


def fleet_legacy_plan(root,current):
    verified_code(Path(__file__).resolve().parents[2]);p=profile();key=str(Path(root));require(key in p['legacy_calls'],'0017 legacy Call not explicitly pinned')
    row=p['legacy_calls'][key]
    for b in row.values():require(record(b['path'])==b,'0017 legacy Call bytes changed')
    require(row['plan']==record(Path(root)/'plan.json') and row['outcome']==record(Path(root)/'outcome.json'),'0017 legacy Call location differs')
    old=read(row['plan']);require(read(row['outcome'])['status']=='completed' and 'fleet_transport_policy' not in old,'0017 incomplete or prospective Call cannot use historical inspection')
    expected=dict(old,fleet_transport_policy='single-dispatch-durable-global-gate/v1',max_transport_retries=0)
    require(canonical(current)==canonical(expected),'0017 historical Call identity/budget/configuration differs')
    return old


def require_fleet_custody(root):
    """Check the selected live central claim before owning permit mutation."""
    from scripts.fleet import model_gate
    require(model_gate.configured(),'0017 pre-review adoption requires live fleet custody')
    client,owner,_=model_gate.context();require(owner['workbook']=='0017','0017 foreign fleet workbook')
    verified_code(root);status=client.call('status')
    require(status['model_limit']==status['publication_limit']==1,'0017 fleet limits changed')
    rows=[r for r in status['workbooks'] if r['workbook']=='0017'];require(len(rows)==1,'0017 central row missing')
    row=rows[0]
    require(row['state']=='active' and row['owner']==owner['owner'] and row['fence']==owner['fence']
            and row['source_sha256']==SOURCE_SHA and row['version']==Path(root).name,'0017 central source/version/fence/custody differs')
    require(not any(o['state']=='hold' for o in status['operations']),'0017 global operation needs reconciliation')
    # The ordinary heartbeat API checks the secret fencing token on the server;
    # no token is returned or embedded in the successor's public proof.
    client.call('heartbeat',**owner)
    return {'workbook':'0017','owner':owner['owner'],'fence':owner['fence'],'version':row['version']}
