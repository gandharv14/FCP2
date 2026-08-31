---
name: additional-assumptions-dialogue
description: Rewrites Workbook disclosure bullets into a colleagues Q&A file in environment/, points instruction.md at that file, and COPY's it into the Harbor image. Invoked as create-harbor-task step 15.5 on the staged bundle, or standalone on an already-promoted task directory.
disable-model-invocation: true
---

# Additional assumptions as a colleagues Q&A file

Run this as create-harbor-task **step 15.5** on `$STAGED`, or standalone on an
already-promoted task directory. It does not re-detect conventions and does
not inline its loop into `create-harbor-task`. Empty `agent_records` is a
no-op: do not invent a conversation, do not launch either agent, do not touch
the Dockerfile.

The solving agent is a junior on the deal. The file is notes from a conversation
with colleagues. Juniors are building a **new** model from scratch. Seniors
already know the assumptions that should go into it. Never call the work a
rebuild, a restoration, or a recreation of an original model.

- **Juniors:** Analyst, Associate
- **Seniors:** VP, Director, Managing Director

Seniors name the **row label**. They name the **sheet/tab** only when it is
unclear from the thread — if a junior already said the tab, or earlier turns
already fixed it, do not start the answer with `On LBO` / `For Operations`.
They never mention cell or range addresses (`J15`, `J15:S15`,
`'NPV IRR'!J15:S15`). Juniors should not mention them either.

The first turn may be a junior asking, or a senior asking how the new model is
coming. Fillers and clarifiers are scattered where they sound natural. Do not
require one on every claim.

## Resolve paths

Work from `FCP2/`. Accept a shipped task directory.

```bash
TASK=../08_18_34_samples_tasks_outputs_unified/0514-outputs
WB=$(python3 -c "from pathlib import Path; print(Path('$TASK').name.split('-')[0])")
S=.cursor/skills/additional-assumptions-dialogue/scripts
RUN="runs/$WB-additional-assumptions"
CLAIMS="$RUN/claims.json"
PACK="$RUN/writer_pack.json"
TEMPLATE="$RUN/draft_template.md"
SLOTS="$RUN/slots.json"
FILLED="$RUN/draft.filled.md"
```

Required inputs:

- `$TASK/instruction.md` with `## Workbook disclosure` (or already-extracted claims)
- `$TASK/tests/disclosure.json` with `agent_records`
- `$TASK/environment/Dockerfile` using `WORKDIR /app` and a workbook `COPY`
- no bare `docker_image` in `$TASK/task.toml`

```bash
python3 $S/extract_claims.py --task-dir "$TASK" --out "$RUN"
python3 $S/compose_draft.py --claims "$CLAIMS" --pack "$PACK" --out "$RUN"
```

If `claims.json` reports `"empty": true`, stop before `compose_draft.py`.

`compose_draft.py` deterministically renders `$TEMPLATE` (`draft_template.md`)
and `$SLOTS` (`slots.json`) once per task, before round 1. The template fixes
every structural line of the draft: every `<!-- claim:RECORD_ID -->` comment
with the real record id, every `**Title:**` speaker line, the sheet sentence
at each tab's **first mention** (later claims on that tab get a bare speaker
line, so a redundant lead-in cannot be rendered), and the blank-line layout.
The only writable lines are the `{{SLOT:<id>}}` lines — one prose slot per
turn. Each slot's required must_say facts and sheet context sit in the
adjacent `<!-- slot:... -->` comment and in `slots.json` (slot id →
`must_say`, `sheet`, `sheet_context`, `claim_ids`, `speaker`, `guidance`).

## Loop (hard cap = 2)

1. Writer fills the slots of `$TEMPLATE` → `$FILLED` (`draft.filled.md`;
   keep `$RUN/draft.filled.r1.md`). It may only replace `{{SLOT:...}}` lines
   with prose — no other edit.
2. `fill-check` verifies the filled draft is **byte-identical to the template
   outside the slots**, strips the `<!-- slot:... -->` scaffolding, writes the
   clean dialogue to `$RUN/draft.md` (keep `$RUN/draft.r1.md`), and runs the
   same mechanical check-draft on it. Exit 2 = structural drift, and the
   error names the first differing draft line. Exit 3 = structure held but
   check-draft failed.
3. On structural drift (exit 2), re-prompt the **same** writer **once**, same
   round, appending the fill-check error verbatim, then re-run `fill-check`.
   **One structural retry per round**; if the second attempt still drifts,
   the round fails. This replaces the old malformed-draft retry —
   `dialogue has no speaker turns`, `no claim comments`, and
   `unknown claim comment` cannot survive fill-check.
4. Paraphrase pass: a **fresh** `gpt-5.6-sol-high` agent rewrites **senior slot
   prose only** in `$FILLED` (keep the incoming file as
   `$RUN/draft.filled.pre-paraphrase.md`, and the pre-paraphrase clean draft
   as `$RUN/draft.pre-paraphrase.md`). Re-run `fill-check` → `$RUN/draft.md`.
   Structural drift here spends the same single per-round retry.
5. Mechanical `check-draft` on `$RUN/draft.md` (unchanged; fill-check already
   ran it, this re-run is the recorded gate). If it fails, still send the
   draft to the reviewer unless the file is empty.
6. Independent reviewer writes `$RUN/review.r1.json` (copy to `$RUN/review.json`).
   Immediately run mechanical `check-review` on it. If `check-review` reports
   schema faults (missing `must_say`, renamed keys such as `claim_coverage` or
   `ordered_claims`, wrong value types), re-prompt the **same** reviewer
   subagent **once** with the fault list and the literal JSON template,
   requiring it to rewrite the same file, then re-run `check-review`. One
   schema retry only; if it is still malformed, the round fails.
7. If that review **passes**, apply and stop.
8. If it **fails** and this was round 1: the writer fills a **fresh copy of
   the template** again, given the writer pack, `$SLOTS`, `$RUN/draft.r1.md`,
   and `$RUN/review.r1.json` → `$FILLED`, then steps 2–4 again (fresh
   structural retry). Copy the clean output to `$RUN/draft.r2.md` as well as
   `$RUN/draft.md`.
9. Reviewer scores the new draft → `$RUN/review.r2.json` (same `check-review`
   gate and single schema retry, with `--round 2`).
10. After two rounds, apply the last draft only if the full independent review
    passes: both **accuracy and naturalness** must pass. Do not package
    `review_passed: false` or `draft_passed: false`. Do not start a third write.

The reviewer never edits the draft. The writer never applies. The retries fix
malformed output only — they never relax what fill-check, check-draft,
check-review, or apply enforce.

```bash
python3 $S/validate_dialogue.py fill-check \
  --task-dir "$TASK" --template "$TEMPLATE" --draft "$FILLED" \
  --claims "$CLAIMS" --out "$RUN/draft.md" \
  --report "$RUN/fill-check.json"

python3 $S/validate_dialogue.py check-draft \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --report "$RUN/draft-check.json"

python3 $S/validate_dialogue.py check-review \
  --claims "$CLAIMS" --review "$RUN/review.r1.json" --round 1 \
  --report "$RUN/review-check.r1.json"

python3 $S/validate_dialogue.py apply \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --review "$RUN/review.json" --round 1 \
  --report "$RUN/apply.json"
```

Use `--round 2` on the second apply. Full review still must pass. These block
in both rounds: missing `must_say`, missing
row label, missing sheet when the thread has not already named the tab,
unknown or leftover speaker titles, empty/stale review coverage,
**any cell/range token in a senior turn, and any whole-column (`A:A`,
`$V:$V`, `'Op Loan 1'!$D:$D`) or whole-row (`3:3`) reference in a senior
turn**. Do not replace working
disclosure bullets with a leaking or incomplete Q&A file. If apply fails, the
scripts roll back `instruction.md`, `Dockerfile`, `task.toml`, the notes file,
and `tests/dialogue-applied.json`.

## Pass 1 — writer

Launch **one** `generalPurpose` subagent, model `gpt-5.6-sol-high`. Give it
`$TEMPLATE` (`draft_template.md`), `$SLOTS` (`slots.json`), and `$PACK`
(`writer_pack.json`). On round 2 also give `$RUN/draft.r1.md` and
`$RUN/review.r1.json`. It writes only `$FILLED` (`draft.filled.md`).
No workbooks. No apply.

Do not give the writer `claims.json` (it contains `reviewer_only` cells), the
golden, formulas, evidence, graded targets, or catalogue ids.

Use this prompt:

```text
You are writing notes from a conversation among colleagues on a deal team.
Juniors (Analyst, Associate) are building a new model from scratch. Seniors
(VP, Director, Managing Director) already know the assumptions that should go
into it. This is a new build, not a rebuild.

Read writer_pack.json, draft_template.md, and slots.json. Write the filled
conversation to the given draft path and nothing else.

OUTPUT FORMAT — mechanical, checked byte-for-byte.
The draft's structure is already written for you in draft_template.md: the
claim comments, the speaker lines, the pre-written sheet sentences, and the
blank lines. Copy the template exactly, replacing each line `{{SLOT:<id>}}`
— and ONLY those lines — with one or more lines of prose for that turn.
- Never add, delete, reorder, or edit any other line: not the
  <!-- claim:... --> comments, not the <!-- slot:... --> comments, not the
  **Title:** speaker lines (including any sheet sentence already on them),
  not the blank lines. You cannot add turns.
- slots.json maps each slot id to the must_say facts its prose must express,
  the sheet context, and the claim it belongs to. The same facts sit in the
  <!-- slot:... --> comment above the speaker line.
- Prose lines must not be blank and must not contain headings, HTML
  comments, new **Title:** speaker lines, or leftover {{SLOT:...}} tokens.
A draft that drifts from the template outside the slots is rejected
mechanically with the first differing line, and you get exactly one retry.

HARD RULES for the prose inside the slots
1. Every must_say atom and the row label MUST appear in the senior prose for
   that claim. The template already names each tab at its first mention; when
   the slot comment says the tab is already established, never open the prose
   with "On {sheet}" / "For {sheet}" / "In {sheet}".
2. `spoken` is the spec, not the line to paste. Paraphrase it into Slack:
   short sentences, contractions, varied order. Do not add or drop
   operators. If the card says last period / this period / locked input,
   those words (or close equivalents) must appear in the senior prose. Keep
   lower-bound, upper-bound, and result locked inputs distinct when the card
   names those roles; never collapse them into "that input." Keep first and
   second locked inputs or input blocks distinct, and preserve any required
   row count and corresponding-value operation.
   FORBIDDEN senior shapes: "On {sheet}, the row labelled \"X\" is copied
   across the forecast:"; pasting `spoken`; "use this copied-column
   calculation"; paren-AST ("multiply (take the").
3. Seniors MUST NOT mention cell or range addresses (J15, J15:S15, Sheet!A1,
   'NPV IRR'!J15:S15) or whole-column / whole-row references (A:A, $V:$V,
   'Op Loan 1'!$D:$D, 3:3). Juniors should not mention them either. Location
   is the tab name plus the visible row label. Never write A1, `cell`,
   `column letters`, or `range`.
4. Do not print catalogue ids, underscored tokens, or graded numbers.
5. Alternatives are unspoken unless the senior immediately closes them.
6. Do not add modelling facts that are not on a claim card.
7. Sound like a live Slack thread, not a bullet list, not an instruction
   ("you must treat..."), and not a paren-AST dump. Backchannel ("Got it —
   one more thing...") lives inside the slot prose; vary the junior
   questions so the file does not read as a checklist.
8. Never describe the work as a rebuild, restoration, or recreation of an
   original model. Do not use rebuild, rebuilding, rebuilt, restore,
   restoring, restored, original model, or original logic. Juniors are
   asking how to build the row; seniors are giving the assumption.
9. If this is round 2, read the previous draft and the reviewer's findings
   and fix every accuracy and naturalness issue they named — still by
   filling a fresh copy of the template.

Output only the filled Markdown conversation. End with one newline.
```

## Pass 1b — paraphrase seniors

Launch a **fresh** `generalPurpose` subagent, model `gpt-5.6-sol-high`, that did
not write the draft. Give it `$PACK`, `$SLOTS`, `$TEMPLATE`, and the current
`$FILLED` (`draft.filled.md`). It rewrites **senior slot prose only** and
writes only `$FILLED`. Copy the incoming file to
`$RUN/draft.filled.pre-paraphrase.md` first, then re-run `fill-check` on its
output to regenerate `$RUN/draft.md`.

Do not give this agent `claims.json`, the golden, formulas, or evidence.

Use this prompt:

```text
You are rewriting senior answers in notes from a deal-team Slack thread.
Juniors stay as they are. You change the senior prose only.

Read writer_pack.json, slots.json, draft_template.md, and the current filled
draft. The draft was produced by filling the template's {{SLOT:...}} lines;
slots.json tells you which slots are senior (kind "senior"). Rewrite the
prose of every senior slot in place. Every other line — claim comments
(<!-- claim:... -->), slot comments (<!-- slot:... -->), speaker lines
including any pre-written sheet sentence on them, junior prose, and blank
lines — must stay byte-identical. Write the full file back to the given
draft path. A structural edit is rejected mechanically with the first
differing line.

HARD RULES
1. Every must_say atom and the row label must still appear in the senior
   prose for that claim. The template already names each tab at first
   mention; when the slot comment says the tab is already established, never
   open the prose with "On {sheet}" / "For {sheet}" / "In {sheet}".
2. spoken is the spec, not the line to paste. Say the same operators in
   different sentences. Vary order and wording across claims.
3. FORBIDDEN in any senior prose:
   - "is copied across the forecast"
   - pasting the card's spoken text
   - "use this copied-column calculation"
   - paren-AST ("multiply (take the")
   - A1 / cell / range addresses
   - whole-column or whole-row references (A:A, $V:$V, 'Op Loan 1'!$D:$D, 3:3)
   - rebuild, restore, original model, original logic
4. If the card says last period / this period / locked input / floor / flip,
   those words (or close equivalents) must remain. Lower-bound, upper-bound,
   and result locked inputs must remain three distinct roles. First and second
   locked inputs or blocks, row counts, and corresponding-value operations
   must also remain distinct.
5. No new modelling facts. No dropped operators.
6. Slack: contractions, short sentences. Prose may span several lines within
   its slot, but you cannot add turns. Do not make juniors ask again.

Output only the filled Markdown conversation. End with one newline.
```

## Pass 2 — independent reviewer

Launch a **fresh** `generalPurpose` subagent, same model, that did not write
the draft. It must not edit the draft or the task bundle. It writes only
`$RUN/review.rN.json`.

Inputs (and nothing else): `$CLAIMS` (full file, including `reviewer_only`),
the `disclosure_body` from `claims.json`, the current `draft.md`, and the
rubric below.

Do not give this agent the golden workbook, formulas, evidence, graded targets,
or the writer's prompt.

Use this prompt:

```text
You are reviewing notes from a deal-team conversation. You did not write the
draft. Do not edit it. The juniors are building a new model from scratch.

Read claims.json, its disclosure_body, and draft.md. Score accuracy and
naturalness. Write JSON to the given review path and nothing else:

{
  "agent_model": "gpt-5.6-sol-high",
  "round": <1 or 2>,
  "accuracy": {
    "verdict": "pass" or "fail",
    "claims": [{"record_id": "...", "must_say": "entailed|missing|contradicted", "findings": []}],
    "extras": [],
    "cell_refs_in_senior_turns": []
  },
  "naturalness": {
    "verdict": "pass" or "fail",
    "findings": []
  },
  "passed": true or false
}

The JSON shape above is a strict machine-read schema, not a suggestion:
- Top-level keys are exactly agent_model, round, accuracy, naturalness,
  passed. Never rename them — no "model", no "claim_coverage", no
  "ordered_claims", no extra top-level arrays.
- accuracy.claims is the only per-claim list. Each entry is one object with
  "record_id" (copied character-for-character from claims.json, in
  claims.json order, once per claim even when record_ids repeat) and
  "must_say", whose value is a single string verdict: exactly "entailed",
  "missing", or "contradicted". Never a boolean, list, object, or per-atom
  breakdown. Explanations go in that entry's optional "findings" array of
  strings, in accuracy.extras, or in naturalness.findings.
- A review missing any required field is rejected mechanically without being
  read, and the round fails.

Cover every claim in the claims array exactly once, in order, even when
record_ids repeat. passed is true only if both verdicts are pass, every
must_say is entailed, extras is empty, and cell_refs_in_senior_turns is empty.

ACCURACY — fail if any:
- A must_say clause is missing or contradicted in the senior turns for that claim.
- A distinguishing locator from the card is missing (last period / this
  period / locked input / next period / source tab) when the card used it.
- Lower-bound, upper-bound, and result locked inputs are collapsed into one
  ambiguous input.
- First and second locked inputs or input blocks are collapsed, or a required
  block row count or corresponding-value operation is omitted.
- A modelling assertion that is not on any claim card.
- An alternative named and left open.
- Catalogue ids, or a numeric literal that looks like a graded target.
- Missing row label for a claim in a senior turn.
- Missing sheet name in a senior turn when the junior question and prior
  turns did not already name the tab.
- A senior turn that opens with "On/For/In {sheet}" when that tab is
  already clear from the question or the turns above.
- Any A1 / range token (J15, J15:S15, 'Sheet'!A1) in a senior turn.
- A senior turn is still paren-AST (`multiply (take the negative of`).
- The exercise is called a rebuild or restoration (rebuild, restore, original
  model, original logic).

Do not mark required must_say wording as an extra, even if it is
formula-shaped. Those clauses must appear.

NATURALNESS — fail if any:
- Speakers include Senior banker or Senior investor.
- A senior turn is the rendered bullet or the card's `spoken` line with a
  speaker prefix.
- A senior turn uses "is copied across the forecast" or is a near-copy of
  `spoken`.
- A senior turn opens with "On {sheet}" / "For {sheet}" when the junior
  already named the tab.
- A pasted `copied-column` dump or paren-AST calculation.
- Junior questions across the file are the same template repeated, or a checklist.
- The file reads like documentation or an instruction rather than Slack.
- Voice is wrong: a junior sounds like the senior, or a senior sounds like a rubric.
- A filler or clarifier smuggles a modelling fact.
- The work is framed as a rebuild or restoration (rebuild, restore, original
  model, original logic). This must be a new build.

Do not fail because the first speaker is a senior. Do not fail a claim that
is a short two-liner. Do not require a filler or clarifier on every claim
or on a one-claim file. Judge checklist-like repetition holistically.

Process talk is fine. An open alternative is not.
```

Copy the latest review to `$RUN/review.json` only after `check-review` passes
on it (see the Loop). `check-review` failure messages name the missing or
malformed field and the expected schema fragment; when re-prompting the
reviewer, quote them verbatim together with the JSON template above.

## Apply

Apply writes five things, then smokes the main image:

- `environment/additional-assumptions.md` (claim comments stripped)
- `COPY additional-assumptions.md /app/additional-assumptions.md` on the Dockerfile
- `instruction.md`: strip `## Workbook disclosure`; add a must-read sentence in
  `## Input`; insert `## Additional assumptions` before `## Output`
- refresh `[metadata.naturalizer] instruction_sha256`
- `tests/dialogue-applied.json` (mutation marker; verifier-only, not in `/app`)

Pointer copy (both Input and Additional assumptions). Do not say rebuild:

> It records a conversation between colleagues containing assumptions you need to follow.

Then `docker build` `$TASK/environment` and `docker run --rm IMAGE test -f /app/additional-assumptions.md`.
A text-only Dockerfile check is not enough. Build the **main** image, not `mcp-server`.
Apply always proves COPY semantics (sources exist and the Dockerfile copies the notes to `/app`). If the Docker daemon is down, use `--skip-smoke` and re-run:

```bash
python3 $S/validate_dialogue.py smoke --task-dir "$TASK"
```

once Docker is up. Do not treat `--skip-smoke` as the normal path.

Harbor must build the image from the mutated `environment/`. An image built
before this skill will not contain the file.

Do not put the notes in `tests/` (except the mutation marker) or in
`mcp-server/runtime/`. Run this after disclosure, naturalization, MCP oracle,
and grader smoke. After it applies, do **not** re-run any of these on the
mutated bundle — they undo the work or restore `## Workbook disclosure`:

- `disclose.py write` (restores bullets beside the Q&A — dual-shipping)
- `disclose.py verify` (still looks for `## Workbook disclosure`)
- `xl_output_task.py` / `xl_harbor_prep.py` (xlsx-only Dockerfile)
- create-harbor-task steps 11–15 (repackaging, disclose write/verify,
  naturalize, oracle env check before this step)

`plain_eligibility.check_plain_environment` and
`xl_mcp_oracle.check_environment` may run after apply: they allow
`additional-assumptions.md` only when `tests/dialogue-applied.json` exists
and the Dockerfile copies the notes to `/app`.

Read `tests/dialogue-applied.json` before those commands. If it is present,
the bundle was mutated by this skill. Re-run this skill after any full Harbor
repackaging that stages a fresh bundle.

## Done when

- `$TASK/environment/additional-assumptions.md` exists
- Dockerfile copies it to `/app/additional-assumptions.md`
- image smoke passed
- `instruction.md` has no `## Workbook disclosure`
- `## Input` names `additional-assumptions.md`, says working directory, and
  tells the agent to read it before building
- pointer copy says colleagues / assumptions, not senior banker, and does
  not say rebuild or restore
- `review.json` exists for the latest round
- `tests/dialogue-applied.json` exists
