"""Retain the original queued qualification, freshly executed without owning writes."""
from pathlib import Path
import json


def capture(pub, P, request, plan, plan_binding, output):
    output = Path(output)
    pub.require(output.is_absolute() and not output.exists(), 'fresh diagnostic output required')
    pub.require(not any(p.is_symlink() for p in (output, *output.parents)), 'canonical diagnostic output required')
    tracker = request.tracker_root / (plan['workbook'] + '.json')
    before = pub.binding(tracker)
    journal = Path(plan['journal'])
    paths = (journal, request.output, request.publication_root)
    pub.require(not any(p.exists() for p in paths), 'publication already started; reconcile')

    def forbidden(*args, **kwargs):
        raise RuntimeError('qualification diagnostic cannot mutate owning state')

    changed = [(P, name) for name in ('run', 'execute', '_lease_step')]
    changed.append((P.schema, 'cas_mutate'))
    originals = [(obj, name, getattr(obj, name)) for obj, name in changed]
    try:
        for obj, name, _ in originals:
            setattr(obj, name, forbidden)
        P._paths(request, journal / 'owning-publication')
        P._checkpoint(request)
        qualification = P.ready._proof(pub.checked(before), request.selection)
        pub.require(qualification['schema_version'] == 'harbor-complete-readiness/v1'
                    and qualification['workbook'] == plan['workbook']
                    and qualification['stage_sha256'] == request.selection.content.stage_sha256
                    and qualification['publication_ready'] is True
                    and qualification['blockers'] == [], 'original qualification did not pass')
        pub.require(pub.binding(tracker) == before and not any(p.exists() for p in paths),
                    'owning state changed during qualification')
        output.mkdir(mode=0o700)
        def retain(name, value):
            path = output / name
            with path.open('x') as stream:
                path.chmod(0o600)
                json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
                stream.write('\n')
            return pub.binding(path)
        # Keep the original returned report intact, rather than only its counts.
        report = retain('qualification.json', qualification)
        return retain('proof.json', dict(
            status='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED', plan=plan_binding,
            tracker=before, request=P.encode(request), qualification=report,
            models_dispatched=0, lease_mutations=0, publication_mutations=0))
    finally:
        for obj, name, original in reversed(originals):
            setattr(obj, name, original)


def capture_publication(pub, P, request, plan, plan_binding, output, publisher):
    """Run the same original full proof once and retain root-launch metadata."""
    pub.require(pub.binding(Path(pub.__file__)) == publisher
                and pub.checked(plan_binding) == plan, 'publication code or plan differs')
    captured = capture(pub, P, request, plan, plan_binding, output)
    value = pub.checked(captured)
    report = pub.checked(value['qualification'])
    checks = report.get('checks')
    pub.require(isinstance(checks, dict) and bool(checks)
                and all(isinstance(gate, dict) and gate.get('checked') is True
                        for gate in checks.values())
                and isinstance(report.get('evidence'), list) and bool(report['evidence']),
                'complete checked publication gates and evidence required')
    result = dict(status='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED',
        plan=plan_binding, publisher=publisher, ready=plan['ready'], tracker=value['tracker'],
        paths=dict(journal=plan['journal'], output=str(request.output),
                   publication_root=str(request.publication_root)),
        checks=len(checks), evidence=len(report['evidence']), qualification=value['qualification'],
        qualification_capture=captured, models_dispatched=0, publication_mutations=0)
    path = Path(output) / 'publication-proof.json'
    with path.open('x') as stream:
        path.chmod(0o600)
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    return pub.binding(path)
