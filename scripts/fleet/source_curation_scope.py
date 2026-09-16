"""New source-curation scope from a retained, rejected target-cone proof.

This selects whole independent output bands; it never changes source formulas,
values, or the old generation. The result still requires full source validation.
"""
from pathlib import Path
from collections import deque
import hashlib,json,re,tomllib

SCHEMA='fleet-independent-source-curation/v1'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path).absolute()
    require(p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'regular source evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row,json_value=True):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'source evidence changed')
    raw=Path(row['path']).read_bytes()
    return json.loads(raw)if json_value else raw.decode('utf-8')


def derive(segments,curation):
    """Return an exact curation delta and complete original graph witnesses."""
    v=segments['verification'];proof=segments['proof'];targets=v['ordered_output_cells']
    require(v['status']=='fail'and v['passed']is False and v['blocking_reasons']
        and set(v['blocking_reasons'])<={'active_cycle_demonstrated_non_unique','active_cycle_uncertified'},
        'only an explicit cycle-certification rejection is scoped')
    require(proof['closure']['stabilized']is True and proof['closure']['targets_stable']is True,
        'stable original runtime closure required')
    require(v['outputs']==dict(match=len(targets),mismatch=0,unresolved=0,unverifiable=0)
        and len(targets)==len(set(targets))and targets,'complete matching original targets required')
    require(v['provenance']['proof']['strict']is True
        and v['provenance']['proof']['expected_cache_reads']==0
        and v['seeded_inside_output_cone_count']==v['oracle_fallback_inside_output_cone_count']==0,
        'original target proof cannot use formula seeds or an oracle')
    graph=proof['runtime_radj']
    require(isinstance(graph,dict)and all(isinstance(k,str)and isinstance(a,list)
        and all(isinstance(x,str)for x in a)for k,a in graph.items()),'complete retained runtime graph required')
    groups=v['provenance']['cycles']['proof_cycle_groups'];diagnostics=v['iterative_blocks']
    require(isinstance(groups,list)and groups,'complete original cycle groups required')
    seen=set();rejected=[]
    for index,members in enumerate(groups):
        require(members and len(members)==len(set(members))and not(seen&set(members)),
            'cycle groups overlap or repeat')
        seen.update(members)
        matches=[d for d in diagnostics if d['seed']in members]
        require(len(matches)==1,'each retained cycle needs its own original diagnostic')
        d=matches[0]
        require(d['member_count']==len(members)and d['members']==members[:20]
            and d['output_relevant']is True,'cycle diagnostic membership differs')
        safe=(d['certified']is True and d['converged']is True
            and d['uniqueness']!='demonstrated_non_unique'and d['errors']==0
            and d['unresolved']==0 and d['budget_exhausted']is False
            and d['topology_stable']is True and d['targets_stable']is True)
        if not safe:rejected.append(dict(index=index,members=members,diagnostic=d))
    require(rejected,'no rejected cycle to isolate')
    exclusions=[]
    for target in targets:
        parents={target:None};queue=deque([target])
        while queue:
            current=queue.popleft()
            for dep in graph.get(current,[]):
                if dep not in parents:parents[dep]=current;queue.append(dep)
        paths=[]
        for group in rejected:
            cells=set(group['members'])&parents.keys()
            if not cells:continue
            path=[sorted(cells)[0]]
            while parents[path[-1]]is not None:path.append(parents[path[-1]])
            path.reverse();paths.append(dict(cycle=group['index'],path=path))
        if paths:exclusions.append(dict(target=target,paths=paths))
    excluded={r['target']for r in exclusions};selected=[t for t in targets if t not in excluded]
    require(excluded and selected,'scope must exclude rejected dependencies and retain useful targets')
    outputs=segments['outputs'];bands={r['band']:r['cells']for r in outputs}
    require(len(bands)==len(outputs),'original output bands repeated')
    before=tomllib.loads(curation);entries=before['output'];included=[r['band']for r in entries if r['include']]
    require(len(included)==len(set(included))and set(included)==set(bands),
        'original curation differs from the verified output bands')
    require(set(targets)=={c for cells in bands.values()for c in cells},'output cells differ from selected target fingerprint')
    removed=set()
    for band,cells in bands.items():
        intersection=set(cells)&excluded
        require(not intersection or intersection==set(cells),'partial output-band scope requires a separate policy')
        if intersection:removed.add(band)
    # Preserve every byte except the existing include booleans of whole excluded
    # bands. Labels, names, formulas, other bands and advisory scores stay exact.
    blocks=re.split(r'(?m)(?=^\[\[output\]\]\s*$)',curation);changed=0
    for i,block in enumerate(blocks):
        if not block.startswith('[[output]]'):continue
        row=tomllib.loads(block)['output'][0]
        if row['band']in removed:
            require(row['include']is True,'excluded band was not selected')
            blocks[i],count=re.subn(r'(?m)^(include\s*=\s*)true(\s*(?:#.*)?)$',r'\1false\2',block)
            require(count==1,'exact original include field required');changed+=1
    require(changed==len(removed),'curation block coverage differs')
    after=''.join(blocks);expected=json.loads(json.dumps(before))
    for row in expected['output']:
        if row['band']in removed:row['include']=False
    require(tomllib.loads(after)==expected,'curation changes exceed exact selected booleans')
    return after,dict(original_targets=targets,selected_targets=selected,excluded_targets=exclusions,
        excluded_bands=sorted(removed),rejected_cycles=rejected,
        retained_certified_cycles=len(groups)-len(rejected),
        original_target_fingerprint=v['fingerprints']['selected_output_cells'],
        new_source_validation_required=True,source_qualified=False)


def prepare(*,prior,segments,curation,source,snapshot,authorization,output):
    previous=read(prior)
    require(binding(source['path'])==source,'original source changed')
    code=read(snapshot);auth=binding(authorization['path'])
    require(auth==authorization and auth['sha256']==AUTHORIZATION_SHA256,'production authorization differs')
    r=previous['result']
    require(previous['claims']==previous['models']==0 and r['state']=='STOPPED'
        and r['bindings']['source_sha256']==source['sha256']
        and r['bindings']['code_sha256']==code['fingerprint']
        and r['verification_evidence']==segments,'original rejected source generation differs')
    data=read(segments);text=read(curation,False)
    require(Path(curation['path']).parent==Path(segments['path']).parent
        and data['verification']['fingerprints']['curation']['sha256']==curation['sha256'],
        'original generation curation differs')
    new_text,evidence=derive(data,text);out=Path(output).absolute()
    require(not out.exists(),'source scope namespace consumed');out.mkdir(parents=True)
    c=out/'curation.toml';c.write_text(new_text,encoding='utf-8')
    result=dict(schema_version=SCHEMA,prior_source=prior,original_segments=segments,
        original_curation=curation,original_source=source,original_snapshot=snapshot,
        authorization=authorization,helper=binding(__file__),curation=binding(c),evidence=evidence,
        source_changes=0,models=0,old_generation_changes=0)
    p=out/'selection.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    require(binding(source['path'])==source and binding(segments['path'])==segments
        and binding(curation['path'])==curation,'original source evidence changed during selection')
    return binding(p)
