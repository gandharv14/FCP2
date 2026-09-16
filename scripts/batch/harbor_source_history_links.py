"""Recognize authenticated source-inspection links without hiding task history."""
from pathlib import Path
import os
import re

LINK_NAMES = {'current', 'bands.csv', 'segments.json', 'output_candidates.csv', 'lineage.json'}


def verified_inspection_links(base, known_ids, markers):
    """Return only canonical links to one live-verified inspection generation.

    A malformed or obsolete source proof remains conservative prior-identity
    evidence. No arbitrary links or whole history directories are excluded.
    """
    base = Path(base)
    if (base.name not in known_ids or base.parent.name != 'inspection-segments'
            or any(p.is_symlink() for p in (base, *base.parents))):
        return set()
    try:
        current = base/'current'
        if not current.is_symlink():
            return set()
        target = os.readlink(current)
        if not re.fullmatch(r'generations/[0-9a-f]{64}', target):
            return set()
        generation = base/target
        if any(p.is_symlink() for p in (generation, generation.parent)):
            return set()
        # Never let source evidence hide a task or release marker.
        if any(p.name in markers or p.is_symlink() for p in generation.rglob('*')):
            return set()
        from xl_seg.publication import resolve_current_generation
        inspection = base.parent.parent
        resolved, manifest = resolve_current_generation(base, expected_generation_id=generation.name,
            source_path=inspection/'inspection-source'/(base.name+'.xlsx'),
            ast_dir=inspection/'ast'/base.name, require_pass=True, validate_live_evidence=True)
        if resolved != generation or manifest['generation_id'] != generation.name:
            return set()
        allowed = {'current'}
        for name in LINK_NAMES-{'current'}:
            path = base/name
            if (path.is_symlink() and os.readlink(path) == 'current/'+name
                    and path.resolve() == generation/name and name in manifest['artifacts']):
                allowed.add(name)
        return allowed
    except (OSError, ValueError, KeyError, TypeError):
        return set()
