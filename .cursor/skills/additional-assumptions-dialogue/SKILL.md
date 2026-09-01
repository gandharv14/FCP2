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
```

Required inputs:

- `$TASK/instruction.md` with `## Workbook disclosure` (or already-extracted claims)
- `$TASK/tests/disclosure.json` with `agent_records`
- `$TASK/environment/Dockerfile` using `WORKDIR /app` and a workbook `COPY`
- no bare `docker_image` in `$TASK/task.toml`

```bash
python3 $S/extract_claims.py --task-dir "$TASK" --out "$RUN"
```

If `claims.json` reports `"empty": true`, stop.

## Loop (hard cap = 2)

1. Writer produces `$RUN/draft.md` (keep `$RUN/draft.r1.md`).
2. Paraphrase pass: a **fresh** `gpt-5.6-sol-high` agent rewrites **every senior
   turn only**. Juniors, claim comments, and titles stay. Write `$RUN/draft.md`
   (keep the pre-paraphrase file as `$RUN/draft.pre-paraphrase.md`).
3. Mechanical `check-draft`. If it fails, still send the draft to the reviewer
   unless the file is empty.
4. Independent reviewer writes `$RUN/review.r1.json` (copy to `$RUN/review.json`).
5. If that review **passes**, apply and stop.
6. If it **fails** and this was round 1: launch the writer again with the writer
   pack, `$RUN/draft.r1.md`, and `$RUN/review.r1.json`. Output `$RUN/draft.r2.md`
   (also copy to `$RUN/draft.md`), then run the paraphrase pass again.
7. Reviewer scores the new draft → `$RUN/review.r2.json`.
8. After two rounds, apply the last draft only if the full independent review
   passes. Do not package `review_passed: false` or `draft_passed: false`.
   Do not start a third write.

The reviewer never edits the draft. The writer never applies.

```bash
python3 $S/validate_dialogue.py check-draft \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --report "$RUN/draft-check.json"

python3 $S/validate_dialogue.py apply \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --review "$RUN/review.json" --round 1 \
  --report "$RUN/apply.json"
```

Use `--round 2` on the second apply. Full review still must pass. These block
in both rounds: missing `must_say`, missing
row label, missing sheet when the thread has not already named the tab,
unknown or leftover speaker titles, empty/stale review coverage,
**and any cell/range token in a senior turn**. Do not replace working
disclosure bullets with a leaking or incomplete Q&A file. If apply fails, the
scripts roll back `instruction.md`, `Dockerfile`, `task.toml`, the notes file,
and `tests/dialogue-applied.json`.

## Pass 1 — writer

Launch **one** `generalPurpose` subagent, model `gpt-5.6-sol-high`. Give it only
`$PACK` (`writer_pack.json`). On round 2 also give `$RUN/draft.r1.md` and
`$RUN/review.r1.json`. It writes only `$RUN/draft.md`. No workbooks. No apply.

Do not give the writer `claims.json` (it contains `reviewer_only` cells), the
golden, formulas, evidence, graded targets, or catalogue ids.

Use this prompt:

```text
You are writing notes from a conversation among colleagues on a deal team.
Juniors (Analyst, Associate) are building a new model from scratch. Seniors
(VP, Director, Managing Director) already know the assumptions that should go
into it. This is a new build, not a rebuild.

Read writer_pack.json. Write a Markdown conversation to the given draft path
and nothing else.

HARD RULES
1. Speakers must be exactly these titles, bolded as **Title:**
   Juniors: Analyst, Associate. Seniors: VP, Director, Managing Director.
   A file may use one or both junior titles and should rotate senior titles
   when there is more than one claim. Do not use Senior banker or Senior investor.
2. The first turn may be a junior asking about a row, or a senior asking how
   the new model is coming ("Where are we on this?"). A senior kickoff is
   not a must_say turn.
3. One exchange per claim, in claim order. Place <!-- claim:RECORD_ID -->
   immediately before that claim's first conversational turn. A kickoff before
   the first claim comment is allowed.
4. Every must_say atom and the row label MUST appear in a senior turn for
   that claim. A clarifier does not count as coverage. Name the sheet only
   if the junior question and the turns above have not already made the tab
   obvious. Do not open with "On {sheet}" / "For {sheet}" / "In {sheet}"
   when the analyst or associate already said the tab.
5. `spoken` is the spec, not the line to paste. Paraphrase it into Slack:
   short turns, contractions, varied sentence order. Do not add or drop
   operators. If the card says last period / this period / locked input,
   those words (or close equivalents) must appear in the senior turn. Keep
   lower-bound, upper-bound, and result locked inputs distinct when the card
   names those roles; never collapse them into "that input." Keep first and
   second locked inputs or input blocks distinct, and preserve any required
   row count and corresponding-value operation.
   FORBIDDEN senior shapes: "On {sheet}, the row labelled \"X\" is copied
   across the forecast:"; pasting `spoken` with a speaker prefix; "use this
   copied-column calculation"; paren-AST ("multiply (take the").
6. Scatter fillers ("Got it.", "Makes sense.") and senior clarifiers
   ("Which tab should I be looking at?") irregularly where they sound natural.
   Do not put one on every claim. Do not require one on a one-claim file.
   Fillers and clarifiers add no modelling facts and name no new rows.
7. Seniors MUST NOT mention cell or range addresses (J15, J15:S15, Sheet!A1,
   'NPV IRR'!J15:S15). Juniors should not mention them either. Location is
   the tab name plus the visible row label. Never write A1, `cell`, or `range`.
8. Do not print catalogue ids, underscored tokens, or graded numbers.
9. Alternatives are unspoken unless the senior immediately closes them.
10. Do not add modelling facts that are not on a claim card.
11. Sound like a live Slack thread, not a bullet list, not an instruction
    ("you must treat..."), and not a paren-AST dump
    ("multiply (take the negative of"). A short two-line claim is fine if
    other turns have backchannel.
12. Never describe the work as a rebuild, restoration, or recreation of an
    original model. Do not use rebuild, rebuilding, rebuilt, restore,
    restoring, restored, original model, or original logic. Juniors are
    asking how to build the row; seniors are giving the assumption.
13. If this is round 2, read the previous draft and the reviewer's findings
    and fix every accuracy and naturalness issue they named.

Output only the Markdown conversation. End with one newline.
```

## Pass 1b — paraphrase seniors

Launch a **fresh** `generalPurpose` subagent, model `gpt-5.6-sol-high`, that did
not write the draft. Give it `$PACK` and the current `$RUN/draft.md`. It
rewrites **every senior turn** and writes only `$RUN/draft.md`. Copy the
incoming draft to `$RUN/draft.pre-paraphrase.md` first.

Do not give this agent `claims.json`, the golden, formulas, or evidence.

Use this prompt:

```text
You are rewriting senior answers in notes from a deal-team Slack thread.
Juniors stay as they are. You change senior turns only.

Read writer_pack.json and the current draft. Rewrite every senior (VP,
Director, Managing Director) turn. Keep junior turns, claim comments
(<!-- claim:... -->), speaker titles, and claim order. Write the full
conversation back to the given draft path.

HARD RULES
1. Every must_say atom and the row label must still appear in a senior turn
   for that claim. Repeat the sheet only if the junior question and the
   turns above left the tab unclear. Do not start with "On {sheet}" /
   "For {sheet}" / "In {sheet}" when the junior already named it.
2. spoken is the spec, not the line to paste. Say the same operators in
   different sentences. Vary order and wording across claims.
3. FORBIDDEN in any senior turn:
   - "is copied across the forecast"
   - pasting the card's spoken text
   - "use this copied-column calculation"
   - paren-AST ("multiply (take the")
   - A1 / cell / range addresses
   - rebuild, restore, original model, original logic
4. If the card says last period / this period / locked input / floor / flip,
   those words (or close equivalents) must remain. Lower-bound, upper-bound,
   and result locked inputs must remain three distinct roles. First and second
   locked inputs or blocks, row counts, and corresponding-value operations
   must also remain distinct.
5. No new modelling facts. No dropped operators.
6. Slack: contractions, short turns. A senior may split one claim across
   two turns. Do not make juniors ask again.

Output only the Markdown conversation. End with one newline.
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
    "claims": [{"record_id": "...", "must_say": "entailed|missing|contradicted"}],
    "extras": [],
    "cell_refs_in_senior_turns": []
  },
  "naturalness": {
    "verdict": "pass" or "fail",
    "findings": []
  },
  "passed": true or false
}

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

Copy the latest review to `$RUN/review.json`.

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
