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

Completed instructions must never contain `[[Fnn]]` transport markers. For a
retained historical submission affected by the old assembly defect, the
orchestrator may use `scripts/span_restoration.py` with the supported recovery
module. `restore` requires an exact state hash, original independent review
request/response, unchanged submitted bodies and regenerated span map. It
publishes a separate immutable candidate only when restoration removes wrappers
alone, reports zero drift and passes current full validation. It does not reset
or extend the writer budget.

The orchestrator independently reviews whether the original clause review still
applies and writes the exact hash-bound
`naturalization-span-review-applicability/v1` record. `accept_review` validates
that record before ordinary `apply` and `verify-applied`. This is an explicit
prior-review applicability decision, not a new model response. Preserve original
attempt artifacts and rerun all affected downstream gates. Missing or changed
evidence, altered prose and noncanonical spans remain failures.

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

When the authorized model invocation uses the configured Labelbox proxy,
record the actual endpoint with
`python3 "$RECOVERY" bind-endpoint "$NAT_RUN" --endpoint https://litellm.labelbox.com`
before applying. This preserves submitted attempts and accepted semantic
review; it does not reset either budget. Save the independent writer and
reviewer request/response evidence and use `openai/gpt-5.6-sol` with high
reasoning effort. Legacy Cursor subagent runs retain `cursor-subagent` metadata.

If an authorized upstream correction changes the immutable instruction source,
initialize a fresh recovery directory and carry forward the consumed writer
budget before making another model call:

```bash
python3 "$RECOVERY" inherit-budget "$NAT_RUN" \
  --previous-state-dir "$PREVIOUS_NAT_RUN" \
  --previous-state-sha256 "$RECORDED_PREVIOUS_STATE_SHA256" \
  --reason "Describe the reviewed upstream correction"
```

Use the previous state digest already recorded in the tracker or handoff. The
operation verifies and archives the prior evidence unchanged, including chained
inheritance. Read `next_attempt`; one previously consumed attempt leaves only
attempt two, and two consumed attempts leave the new run exhausted. Old prose
and semantic decisions are not reused for the corrected source. Do not simulate
a semantic rejection or initialize a new budget to bypass this ceiling.

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

## Restore, validate and apply

Submit the exact two marked writer bodies through the frozen submission path:

```bash
python3 "$RECOVERY" submit-frozen "$NAT_RUN" \
  --preamble "$PREAMBLE" --input "$INPUT_BODY"
```

This command reads `$NAT_RUN/writer_source.md` and
`$NAT_RUN/frozen_spans.json` and independently regenerates both from the immutable
source snapshot. It retains the exact writer bodies, assembles the complete
marked document from untouched source bytes, and restores canonical wrappers.
The attempt directory retains `candidate.marked.md`, `candidate.restored.md`,
`restore.json`, and the exact authoring inputs. It selects `candidate.md` only
when restoration has zero errors and zero drift, then runs the full deterministic
validator. BOM, newline style, headings, and protected sections are preserved.

Missing, duplicated, unknown, malformed, or edited spans fail the same bounded
writer attempt. Never repair a writer's raw bodies, run an unbound full-document
restore, switch submission modes to retry an attempt, or review a marked
candidate. Resuming the same staged submission requires identical raw bodies,
mode and frozen authoring inputs. Ordinary `submit` remains available only for
unmarked authoring workflows and does not reinterpret historical attempts.

Read the attempt candidate and validation report. Only after `valid: true`, perform a fresh independent clause-by-clause
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
the attempt-local marked/restored candidates, `restore.json`, and `validation.json` as diagnostics
and leave the staged instruction unchanged. Validator failure messages state
the check name, the expected text, and what was found — quote them verbatim
when reporting a blocker. Do not fall back to an earlier naturalizer and do
not promote an unreviewed rewrite.
