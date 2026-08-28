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
SOURCE="$NAT_RUN/source.snapshot.md"
WRITER_SOURCE="$NAT_RUN/writer_source.md"
SPAN_MAP="$NAT_RUN/frozen_spans.json"
MARKED="$NAT_RUN/candidate.marked.md"
CANDIDATE="$NAT_RUN/candidate.md"
REPORT="$NAT_RUN/validation.json"
RECOVERY=.cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py
```

Never edit a promoted task. The staged `instruction.md` must already contain
the research section, target table, custom-formula hints, modelling conventions,
and output contract that will ship.

Initialize or resume the code-owned state. This never overwrites a different
source snapshot:

```bash
python3 "$RECOVERY" init "$STAGED/instruction.md" \
  --state-dir "$NAT_RUN" \
  --instruction "$STAGED/instruction.md" \
  --task-toml "$STAGED/task.toml" \
  --answer-key "$STAGED/tests/answer_key.json"
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

Use at most two total attempts. Each attempt launches a fresh `generalPurpose`
subagent with model `gpt-5.6-sol-high`. Give it the frozen writer source
(`$WRITER_SOURCE`) and two attempt paths. It never sees the unmarked source
and must not edit the staged bundle.

Writer contract `finance-instruction-naturalizer-v3` (the `PROMPT_VERSION`
recorded by `validate_instruction_rewrite.py`; v3 added frozen numbers, cell
references, URLs, and inline code):

```bash
PREAMBLE="$NAT_RUN/attempt-01/preamble_body.md"
INPUT_BODY="$NAT_RUN/attempt-01/input_body.md"
```

For attempt two, use `attempt-02`. The model must write only those two editable
bodies. It must not reproduce headings, protected sections, or edit the staged
bundle. Use this prompt:

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
3. Return exactly two files: the opening body and the body beneath `## Input`.
   Do not include either heading or any other section.
4. Within those bodies, reproduce verbatim every inline-code
   span, filename, path, URL, service or tool name, sheet name, cell reference,
   range, output label, period, unit, count, threshold, formula, and number.
   Numbers, cell references, URLs, and inline code arrive already wrapped in
   frozen-span markers; rule 2 governs them. Keep every frozen-span marker
   in the bodies you write.
5. Keep all source categories and finance terminology, including lists such as
   market rates, tax rates, macro assumptions, contractual terms, and opening
   balances.
6. Preserve whether content is present, blank, removed, available only through
   a service, verified absent, optional, required, or prohibited.
7. Do not remove repetition when it independently communicates a requirement.

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

Submit the two bodies. Code reconstructs the full document from untouched source
bytes, preserving the BOM, newline style, headings, and protected sections.
The deterministic validator still runs in full — freezing makes compliance
easier, not the checks weaker:

```bash
python3 "$RECOVERY" submit "$NAT_RUN" \
  --preamble "$PREAMBLE" --input "$INPUT_BODY"
```

Read the attempt candidate and validation report. Perform clause-by-clause
semantic review of the two editable regions. If attempt one passed mechanically
but failed semantic review, record the rejection:

```bash
python3 "$RECOVERY" reject "$NAT_RUN" \
  --reason-code semantic_mismatch \
  --message "<specific omitted, weakened, or added claim>"
```

Then launch one fresh attempt using the original source and the recorded reason
codes. A mechanically rejected attempt is retried only when `state.json` reports
`retry_ready`. Never edit or feed the failed candidate into attempt two. Stop
after attempt two fails.

Only after deterministic and semantic review pass, bind that approval to the
selected candidate and apply it:

```bash
python3 "$RECOVERY" accept "$NAT_RUN" \
  --reviewer "main-agent" \
  --message "Clause-by-clause semantic review passed"
python3 "$RECOVERY" apply "$NAT_RUN"
python3 "$RECOVERY" verify-applied "$NAT_RUN"
```

Require `valid: true`, `applied: true`, model `gpt-5.6-sol-high`, matching
source/candidate hashes, protected-byte checks, exact-token checks, semantic
anchor checks, and no new answer-value occurrence. `instruction.md` and
`task.toml` are one journaled transaction; an interrupted apply must roll back
or reconcile before continuing.

If generation, restore, deterministic validation, or semantic review fails,
stop. Retain the immutable source snapshot, both attempt directories, state,
reports, apply journal, `writer_source.md`, `frozen_spans.json`,
`candidate.marked.md`, `restore.json`, and `validation.json` as diagnostics
and leave the staged instruction unchanged. Validator failure messages state
the check name, the expected text, and what was found — quote them verbatim
when reporting a blocker. Do not fall back to an earlier naturalizer and do
not promote an unreviewed rewrite.
