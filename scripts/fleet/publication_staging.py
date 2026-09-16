"""Explicit immutable root-publication inputs for the native admission reader.

Task-owned READY, tracker, process and outcome records remain native inputs;
they are never copied over by this preparation. This is not a recursive walk
through arbitrary historical artifact bindings.
"""
from pathlib import Path
from scripts.fleet.migration import binding,checked,retain


def child_selection_probe(text):
    """Exercise the actual child adapter before disabling the original P writes."""
    anchor='P.run=P.execute=P._lease_step=P.schema.cas_mutate=no'
    if text.count(anchor)!=1:raise ValueError('unknown publication proof mutation boundary')
    selected="""if any(plan.get(k)is not None for k in ('queued_lease','completed_normalization','source_revision','semantic_continuation','naturalization_continuation','mechanical_tail','validation_runtime','preserved_gate','model_tail','completed_pre_dispatch')):
 reader_inputs=pub.fresh_publication.reader_evidence(pub,plan)
 selected_runner=pub.publication_child_adapter.select(P,reader_plan=PLAN,child_wrapper=PUBLISHER,reader_evidence=reader_inputs)
 assert selected_runner.__code__ is P.run.__code__ and callable(selected_runner.declared_runner)
"""
    return text.replace(anchor,selected+anchor)


def direct_child_selection_probe(text):
    """Check the retained-warning adapter before capturing owning qualification."""
    anchor=' start=time.monotonic();result=Q.capture_publication('
    if text.count(anchor)!=1:
        raise ValueError('unknown direct publication qualification boundary')
    selected=""" selected_runner=pub.publication_child_adapter.select(env[2],reader_plan=row,child_wrapper=PUBLISHER,reader_evidence=pub.warning_publication.evidence(plan))
 assert selected_runner.__code__ is env[2].run.__code__ and callable(selected_runner.declared_runner)
"""
    return text.replace(anchor,selected+anchor)


def rows(profile,*,_ancestry=()):
    if profile['path'] in _ancestry or len(_ancestry)>4:
        raise ValueError('publication staging recovery ancestry is invalid')
    p=checked(profile);manifest=checked(p['implementation']);selected=[profile]
    for key in ('plan','publisher','implementation','proof','guard','capacity_policy'):
        selected.append(p[key])
    for key in ('process_copy','pool_completion','input_history','completed_ready','qualification_capture'):
        if p.get(key) is not None:selected.append(p[key])
    if p.get('input_history') is not None:
        selected.append(checked(p['input_history'])['archive'])
    if p.get('version_custody'):
        selected.append(p['version_custody'])
        selected.extend(item['local'] for item in checked(p['version_custody'])['files'])
    selected.extend(manifest['dependencies'].values())
    guard=checked(p['guard'])
    if guard.get('probe') is not None:selected.append(guard['probe'])
    if p.get('qualification_capture'):
        capture=checked(p['qualification_capture'])
        selected.extend(capture[key] for key in ('capture','qualification'))
        if capture.get('helper') is not None:selected.append(capture['helper'])
    if p.get('preowning_recovery'):
        edge=checked(p['preowning_recovery'])
        if edge.get('schema_version')=='fleet-publication-preowning-queued-recovery/v1':
            if set(edge)!={'schema_version','prior_profile','reader','completion','custody'}:
                raise ValueError('unknown queued preowning staging certificate')
            selected.extend([p['preowning_recovery'],edge['prior_profile'],edge['reader'],edge['custody']])
            selected.extend(x['copy'] for x in checked(edge['custody'])['files'])
            # The original queued CAS records are native owning evidence. They
            # are read in place, never uploaded over by root staging.
        else:
            if set(edge)!={'schema_version','prior_profile','root_intent','failure_proof','reconciliation'}:
                raise ValueError('unknown preowning staging certificate')
            selected.extend([p['preowning_recovery'],*[v for k,v in edge.items() if k!='schema_version']])
            selected.append(checked(edge['reconciliation'])['intent'])
        selected.extend(rows(edge['prior_profile'],_ancestry=(*_ancestry,profile['path'])))
    if p.get('owning_recovery'):
        edge=checked(p['owning_recovery'])
        if set(edge)!={'schema_version','prior_profile','root_intent','failure_proof','reconciliation','resume','guard_implementation','copies'} or edge['schema_version']not in {'fleet-publication-owning-recovery/v1','fleet-publication-preexecute-recovery/v1'}:
            raise ValueError('unknown owning recovery staging certificate')
        selected.extend([p['owning_recovery'],*[edge[k]for k in ('prior_profile','root_intent','failure_proof','reconciliation','resume','guard_implementation')]])
        selected.append(checked(edge['reconciliation'])['intent'])
        selected.extend(item['local']for item in edge['copies'])
        resume=checked(edge['resume'])
        selected.extend(resume[k]for k in ('prior_plan','failure_proof','reconciliation','policy'))
        selected.append(checked(resume['policy'])['authorization'])
        selected.extend(rows(edge['prior_profile'],_ancestry=(*_ancestry,profile['path'])))
    found={}
    for row in selected:
        if set(row)!={'path','sha256'} or binding(row['path'])!=row:
            raise ValueError('publication staging input changed')
        if row['path'] in found and found[row['path']]!=row:
            raise ValueError('publication staging has conflicting bindings')
        found[row['path']]=row
    return list(found.values())


def prepare(profile,output):
    out=Path(output);selected=rows(profile)
    retain(out/'stage-bindings.json',dict(schema_version='fleet-publication-root-staging/v1',
                                        profile=profile,files=selected))
    probe=out/'verify.py'
    with probe.open('x') as stream:
        stream.write('from pathlib import Path\nimport hashlib,json\nrows='+repr(selected)+
                     '\nfor row in rows:assert hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest()==row["sha256"]\n'+
                     'print(json.dumps(dict(status="PUBLICATION_ROOT_FILES_VERIFIED")))\n')
    return retain(out/'stage-files.json',[*[r['path'] for r in selected],str(probe)])
