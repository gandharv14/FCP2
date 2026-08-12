"""Stage 7: split every component into input, middle, output or scaffolding.

Middle is the intersection of what the inputs reach and what the outputs need.
The leftovers are not swept under the rug -- they are reported in three classes,
because each one means something different about the quality of the segmentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .condense import Condensed
from .frontier import ancestors, descendants

INPUT = "input"
MIDDLE = "middle"
OUTPUT = "output"
SCAFFOLD = "scaffolding"

# Scaffolding sub-classes.
PRESENTATION = "presentation"   # pass-through mirrors lifted out in stage 4
UNUSED_INPUT = "unused_input"   # a source no selected output depends on
DEAD = "dead"                   # fed by inputs but reaching no output
DETACHED = "detached"           # touches neither frontier


@dataclass
class Partition:
    bucket: dict = field(default_factory=dict)
    subclass: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    unfed: set = field(default_factory=set)

    def bands_in(self, cd: Condensed, bucket: str) -> list:
        out = []
        for comp, name in self.bucket.items():
            if name == bucket:
                out.extend(cd.comp_members[comp])
        return sorted(out)


def build(cd: Condensed, inputs: set, outputs: set) -> Partition:
    cone = ancestors(cd, outputs)
    reach = descendants(cd, inputs)
    part = Partition()

    for comp in cd.comp_members:
        if comp in outputs:
            part.bucket[comp] = OUTPUT
        elif comp in inputs:
            part.bucket[comp] = INPUT
        elif comp in cone:
            part.bucket[comp] = MIDDLE
        else:
            part.bucket[comp] = SCAFFOLD
            if not cd.comp_radj.get(comp):
                part.subclass[comp] = UNUSED_INPUT
            elif comp in reach:
                part.subclass[comp] = DEAD
            else:
                part.subclass[comp] = DETACHED

    # Guaranteed empty by construction, but a cheap assertion that the cone closed.
    part.unfed = {c for c in cone if c not in reach and c not in inputs}

    part.counts = {
        INPUT: sum(1 for v in part.bucket.values() if v == INPUT),
        MIDDLE: sum(1 for v in part.bucket.values() if v == MIDDLE),
        OUTPUT: sum(1 for v in part.bucket.values() if v == OUTPUT),
        SCAFFOLD: sum(1 for v in part.bucket.values() if v == SCAFFOLD),
    }
    for name in (UNUSED_INPUT, DEAD, DETACHED):
        part.counts[name] = sum(1 for v in part.subclass.values() if v == name)
    part.counts[PRESENTATION] = len(cd.mirrors)
    return part
