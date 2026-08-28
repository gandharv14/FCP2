---
name: naturalize-finance-task-instruction
description: Rewrites complete Harbor task instructions in natural finance-professional language while preserving all requirements and machine-critical content. Use after a task is fully packaged and before oracle checks, grading, or promotion.
disable-model-invocation: true
---

# Naturalize a finance task instruction

Make the final task brief sound like a senior finance professional briefing
another analyst. This is a zero-loss editing gate, not a summarization step.

## Inputs and authoring paths

Accept a staged Harbor task directory. Set:

```bash
STAGED=tasks_outputs_mcp/0256-outputs
WB=0256
NAT_RUN="runs/$WB-instruction-naturalization"
SOURCE="$NAT_RUN/source.md"
WRITER_SOURCE="$NAT_RUN/writer_source.md"
SPAN_MAP="$NAT_RUN/frozen_spans.json"
MARKED="$NAT_RUN/candidate.marked.md"
CANDIDATE="$NAT_RUN/candidate.md"
REPORT="$NAT_RUN/validation.json"
```

Never edit a promoted task. The staged `instruction.md` must already contain
the research section, target table, custom-formula hints, modelling conventions,
and output contract that will ship.

Create the source snapshot without overwriting an earlier review:

```bash
test ! -e "$NAT_RUN"
mkdir -p "$NAT_RUN"
cp "$STAGED/instruction.md" "$SOURCE"
```

## Freeze protected anchors and tokens

Before launching the rewriter, structurally exclude validator-protected text
from what it may touch. The freeze step extracts every phrase the
deterministic validator will later demand (named example outputs, the `may `
permission-modality token, Input exclusivity and availability wording,
semantic anchors such as `rebuild` and `research data service`, source
categories, the financial model type, and removed-content wording — the same
list `protected_anchor_specs` gives the validator) plus, inside the two
rewriteable regions (the opening prose and `## Input`), every exact-count
token the validator tallies: numeric tokens, cell references, URLs, and
inline-code spans, found with the validator's own regexes
(`EXACT_TOKEN_CHECKS`). Each occurrence is wrapped in immutable
`[[Fnn]]…[[/Fnn]]` markers; overlapping spans are merged into one marker.
Prose-embedded numbers are the point: a sentence like "report the 2 headline
figures" arrives as "report the `[[F03]]2[[/F03]]` headline figures", so the
rewriter can reorder the sentence but cannot lose the token:

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/freeze_protected_spans.py freeze \
  "$SOURCE" \
  --writer-source "$WRITER_SOURCE" \
  --span-map "$SPAN_MAP"
```

This writes the marker-annotated `writer_source.md` (what the rewriter reads)
and `frozen_spans.json` (the canonical text of every span). If freeze fails,
stop; do not hand the raw source to the rewriter.

## Rewrite

Launch exactly one `generalPurpose` subagent with model `gpt-5.6-sol-high`.
Give it the writer-source path (`$WRITER_SOURCE`) and the marked-candidate
path (`$MARKED`). Require it to read the complete writer source, apply the
prompt below, and write only the full marked candidate Markdown to `$MARKED`.
It must not edit the staged bundle and it never sees the unmarked source.

Use this prompt verbatim (writer contract `finance-instruction-naturalizer-v3`,
the `PROMPT_VERSION` recorded by `validate_instruction_rewrite.py`; the v3
contract added frozen numbers, cell references, URLs, and inline code):

```text
You are a senior finance professional editing a spreadsheet-reconstruction task
for another analyst. Rewrite the supplied instruction so its ordinary prose is
direct, natural, and specific to financial modelling.

This is a zero-loss editing task, not a summarization task.

HARD RULES
1. Preserve the task's meaning, scope, deliverables, permissions, prohibitions,
   and required procedures. Never weaken "must", "only", "every", "exactly",
   "do not", or an equivalent requirement.
2. The document contains frozen spans marked [[Fnn]]...[[/Fnn]] (for example
   [[F07]]Rebuild[[/F07]] or [[F03]]2[[/F03]]). Frozen spans cover required
   phrases AND every number, cell reference, URL, and inline-code span in the
   prose you may rewrite. These are immutable. Copy every frozen span —
   opening marker, the text between the markers, and closing marker — into
   your output exactly once, character for character, including any spaces
   inside the markers. Never edit, reword, re-case, split, merge, drop,
   duplicate, or nest a frozen span. Compose your prose around the spans; a
   span may be repositioned within its own sentence or section when your
   sentence order changes, but its content is never yours to touch. If a
   sentence mentions a quantity twice, its two frozen spans must both appear
   exactly once each.
3. Do not add modelling advice, formulas, assumptions, interpretations, facts,
   hints, or answer values.
4. Preserve every Markdown heading and its order.
5. Reproduce every section other than the opening prose and `## Input`
   byte-for-byte. This includes the research service, target table, hints,
   conventions, and output contract.
6. Within the opening prose and `## Input`, reproduce verbatim every inline-code
   span, filename, path, URL, service or tool name, sheet name, cell reference,
   range, output label, period, unit, count, threshold, formula, and number.
   Numbers, cell references, URLs, and inline code arrive already wrapped in
   frozen-span markers; rule 2 governs them.
7. Keep all source categories and finance terminology, including lists such as
   market rates, tax rates, macro assumptions, contractual terms, and opening
   balances.
8. Preserve whether content is present, blank, removed, available only through
   a service, verified absent, optional, required, or prohibited.
9. Do not remove repetition when it independently communicates a requirement.
10. Output the complete rewritten Markdown (with all frozen-span markers still
    in place) and nothing else. End with one newline.

Prefer concise sentences and terminology used by investment banking, private
equity, valuation, FP&A, or project-finance practitioners when appropriate.
If a natural rewrite would make any requirement less explicit, retain the
source wording.
```

Do not ask the subagent to classify formulas, inspect the golden workbook, or
infer workbook facts. Its only input is the marker-annotated instruction.

## Restore frozen spans

Reinsert the canonical protected text and strip the markers before validation:

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/freeze_protected_spans.py restore \
  "$MARKED" \
  --span-map "$SPAN_MAP" \
  --output "$CANDIDATE" \
  --report "$NAT_RUN/restore.json"
```

Restore replaces every `[[Fnn]]…[[/Fnn]]` block with the canonical span text
from `frozen_spans.json` (so a span edited inside its markers is repaired, and
the drift is reported), then fails closed if any span is missing, duplicated,
unknown, or if marker residue remains. Each restore error names the span id,
the exact literal block that was expected, and the validator checks it
protects. If restore fails, re-launch the rewrite subagent once, appending the
restore error messages to the prompt; if it fails again, stop and keep the
diagnostics.

## Validate and apply

Run the deterministic validator on the restored candidate (the validator is
unchanged and still runs in full — freezing makes compliance easier, not the
checks weaker):

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/validate_instruction_rewrite.py \
  "$SOURCE" "$CANDIDATE" \
  --answer-key "$STAGED/tests/answer_key.json" \
  --report "$REPORT"
```

Then read the complete source, candidate, and report. Perform a clause-by-clause
semantic review of the two rewriteable regions. Reject any omitted, weakened,
or added claim even when the deterministic validator passed.

Only after both reviews pass, atomically apply the candidate and update the
task's naturalizer metadata:

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/validate_instruction_rewrite.py \
  "$SOURCE" "$CANDIDATE" \
  --answer-key "$STAGED/tests/answer_key.json" \
  --report "$REPORT" \
  --apply-to "$STAGED/instruction.md" \
  --task-toml "$STAGED/task.toml" \
  --attempts 1
```

Require `valid: true`, `applied: true`, model `gpt-5.6-sol-high`, matching
source/candidate hashes, protected-section checks, exact-token checks, semantic
anchor checks, and no new answer-value occurrence.

If generation, restore, deterministic validation, or semantic review fails,
stop. Retain `source.md`, `writer_source.md`, `frozen_spans.json`,
`candidate.marked.md`, `candidate.md`, `restore.json`, and `validation.json`
as diagnostics and leave the staged instruction unchanged. Validator failure
messages state the check name, the expected text, and what was found — quote
them verbatim when reporting a blocker. Do not fall back to an earlier
scenario-only naturalizer and do not promote an unreviewed rewrite.
