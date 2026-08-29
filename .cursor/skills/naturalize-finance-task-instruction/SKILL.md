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

## Rewrite

Use at most two total attempts. Each attempt launches a fresh `generalPurpose`
subagent with model `gpt-5.6-sol-high`. Give it the immutable source and two
attempt paths:

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
2. Do not add modelling advice, formulas, assumptions, interpretations, facts,
   hints, or answer values.
3. Return exactly two files: the opening body and the body beneath `## Input`.
   Do not include either heading or any other section.
4. Within those bodies, reproduce verbatim every inline-code
   span, filename, path, URL, service or tool name, sheet name, cell reference,
   range, output label, period, unit, count, threshold, formula, and number.
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
infer workbook facts. Its only input is the completed instruction.

## Validate and apply

Submit the two bodies. Code reconstructs the full document from untouched source
bytes, preserving the BOM, newline style, headings, and protected sections:

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

Retain the immutable source, both attempt directories, state, reports, and apply
journal. Do not fall back to an earlier naturalizer and do not promote an
unreviewed rewrite.
