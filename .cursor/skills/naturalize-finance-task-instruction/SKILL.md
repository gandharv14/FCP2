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

## Rewrite

Launch exactly one `generalPurpose` subagent with model `gpt-5.6-sol-high`.
Give it the source path and candidate path. Require it to read the complete
source, apply the prompt below, and write only the full candidate Markdown to
`$CANDIDATE`. It must not edit the staged bundle.

Use this prompt verbatim:

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
3. Preserve every Markdown heading and its order.
4. Reproduce every section other than the opening prose and `## Input`
   byte-for-byte. This includes the research service, target table, hints,
   conventions, and output contract.
5. Within the opening prose and `## Input`, reproduce verbatim every inline-code
   span, filename, path, URL, service or tool name, sheet name, cell reference,
   range, output label, period, unit, count, threshold, formula, and number.
6. Keep all source categories and finance terminology, including lists such as
   market rates, tax rates, macro assumptions, contractual terms, and opening
   balances.
7. Preserve whether content is present, blank, removed, available only through
   a service, verified absent, optional, required, or prohibited.
8. Do not remove repetition when it independently communicates a requirement.
9. Output the complete rewritten Markdown and nothing else. End with one newline.

Prefer concise sentences and terminology used by investment banking, private
equity, valuation, FP&A, or project-finance practitioners when appropriate.
If a natural rewrite would make any requirement less explicit, retain the
source wording.
```

Do not ask the subagent to classify formulas, inspect the golden workbook, or
infer workbook facts. Its only input is the completed instruction.

## Validate and apply

Run the deterministic validator:

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

If generation, deterministic validation, or semantic review fails, stop.
Retain `source.md`, `candidate.md`, and `validation.json` as diagnostics and
leave the staged instruction unchanged. Do not fall back to an earlier
scenario-only naturalizer and do not promote an unreviewed rewrite.
