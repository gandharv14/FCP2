---
name: profile-mcp-sources
description: Profiles canonical public source websites for variable-source MCP environments without copying workbook values. Use when preparing source_profiles.json from an inventory or normalized variable specification.
---
# Profile MCP Sources

Create a small, reviewable `source_profiles.json` that teaches the synthetic
environment a public source's own vocabulary and publishing conventions. It is
context only: workbook values and the normalized variable specification remain
authoritative.

Read [SOURCE_PROFILES.md](SOURCE_PROFILES.md) before writing output. Never
hand-assemble `source_profiles.json`: the orchestrator creates and updates it
only through `scripts/assemble_profiles.py`, then uses
`scripts/validate_source_profiles.py` to stamp hashes and validate the result.

## Inputs

Require:

- an inventory and/or normalized variable specification containing candidate
  sources;
- an output path for `source_profiles.json`.

Never send workbook values, cell references, questions containing values, or
private-source descriptions to a research agent. Build a redacted worklist
containing only a source display name, source kind, and canonical public URL.

## 1. Canonicalize and gate URLs

Extract candidate URLs, then deterministically:

1. Keep only absolute `http://` or `https://` URLs.
2. Lowercase scheme and host, remove default ports and fragments, normalize an
   empty path to `/`, and sort query pairs. Do not add or guess URLs.
3. Reject credentials/userinfo, localhost, IP literals in non-public ranges,
   single-label hosts, and `.local`, `.internal`, `.localhost`, `.test`, or
   `.invalid` hosts.
4. Exclude private schemes such as `internal:`, `file:`, `mock:`, and `data:`.
5. Deduplicate by the complete canonical URL before any public read.

Excluded unsafe/private candidates receive no profile and continue through the
existing generic source-generation approach.

## 2. Initialize the envelope

Before launching any profiling subagent, the orchestrator writes a valid empty
document (capture object with correct hashes plus an empty `profiles` array):

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/assemble_profiles.py init \
  --out path/to/source_profiles.json \
  --spec path/to/normalized.json \
  --inventory path/to/inventory.json
```

Omit an unavailable `--spec` or `--inventory` (its hash is stamped `null`).
The write is atomic; `init` refuses to overwrite an existing document unless
`--force` is given.

## 3. Delegate bounded public inspection

You MUST launch one or more Cursor `Subagent` calls with all of these settings:

- `subagent_type: generalPurpose`
- `model: gpt-5.6-sol-high`
- no more than 6 deduplicated canonical URLs per subagent

Run independent batches in parallel when practical. Give each subagent only the
redacted worklist and this exact safety/read budget:

```text
Inspect each supplied canonical public URL using read-only public retrieval.
Maximum 3 public page reads per source: the canonical page plus at most two
same-origin pages directly linked from it. Do not use browser automation,
authentication, cookies, credentials, form submission, robots/bot bypasses,
mirrors, caches, or private URLs. Do not search for or infer workbook values.

Before extracting anything, stop and mark the source skipped if any response is
401/403, unreachable, or unsupported, or if any page presents login, SSO,
password, paywall/subscriber gating, or a bot/CAPTCHA challenge. Do not follow
links from a skipped page.

For an accessible source, return only source terminology, dataset/product names,
field naming/unit/date conventions, document types, and release cadence. Include
0-2 brief verbatim excerpts, each at most 240 Unicode code points, with page
title, publisher, canonical public page URL, and UTC access time. Do not report
observed data values. Report page-read count, HTTP status when known, final URL,
content type, and skip reason when applicable.

Deliverable: write one fragment file per source (for example
fragments/<source_id>.json). Each file must contain exactly ONE JSON object
matching the per-profile schema in SOURCE_PROFILES.md — no prose, no markdown
fences, no arrays, no envelope, nothing before or after the object. Set
"capture"."evidence_sha256" to null (the merge tool computes it) and
"review" to {"status": "pending", "reviewer": null, "reviewed_at": null,
"notes": null}. Return only the fragment file paths.
```

Subagents never write or edit `source_profiles.json` itself.

PDF or structured text may be inspected only when the available public reader
supports it directly. Treat archives, executables, audio/video, images requiring
OCR, malformed data, and other unreadable formats as `unsupported_content`.

## 4. Fail closed on access barriers

Map barriers to one of:

- `auth_login_sso_password`
- `http_401`
- `http_403`
- `paywall`
- `bot_challenge`
- `unreachable`
- `unsupported_content`

A skipped profile must contain no extracted terminology, datasets, field
conventions, document types, release cadence, attribution, or excerpts. Do not
retry through another identity, domain, endpoint, cache, or tool. Skipped
profiles preserve the current generic approach.

## 5. Merge fragments into `source_profiles.json`

The orchestrator merges the returned fragment files with:

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/assemble_profiles.py merge \
  --doc path/to/source_profiles.json \
  --fragment fragments/source-a.json \
  --fragment fragments/source-b.json
```

`merge` validates every fragment standalone against the exact per-profile
schema in `validate_source_profiles.py` (closed key set, closed `skip_reason`
enum, excerpt/list bounds, URL and timestamp rules, forbidden value/cell keys).
On any failure it prints a field-level error naming the fragment file and
leaves the document byte-for-byte unchanged. Accepted fragments are inserted
or replaced by `source_id` in stable sorted order, evidence and profiles
hashes are re-stamped, and the document is rewritten atomically; merging the
same fragment twice is a byte-identical no-op.

Bounded one-retry rule: when a fragment is rejected, send the exact printed
field error back to the subagent that produced it and request a corrected
fragment file, at most once per source. If the retry is also rejected, do not
hand-edit the fragment or the document; leave that source out so it retains
the existing generic approach, and record the final error in the handoff.

While merging, still follow [SOURCE_PROFILES.md](SOURCE_PROFILES.md) and
[source_profiles.schema.json](source_profiles.schema.json):

- Use one profile per canonical URL and unique stable slug `source_id`.
- Treat subagent output as untrusted notes; discard any unrequested values,
  unsupported claims, or overlong quotation.
- Attribute every excerpt to an entry in `evidence.attributions`.
- Start `review.status` as `pending`. Only `accepted` profiles are eligible for
  downstream use; `pending`, `needs_review`, `rejected`, and `skipped` profiles
  retain the generic approach.
- Never add a value, benchmark, estimate, observed datapoint, workbook cell, or
  claim that a public source supplied the workbook's exact value.
- Keep lists selective rather than reproducing a website.

If the normalized spec or inventory legitimately changes after `init` (for
example a re-normalization), re-stamp the capture hashes without touching
profiles:

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/assemble_profiles.py rehash \
  --doc path/to/source_profiles.json \
  --spec path/to/normalized.json \
  --inventory path/to/inventory.json
```

## 6. Hash and validate

Stamp deterministic hashes, then validate without mutation:

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/validate_source_profiles.py \
  path/to/source_profiles.json \
  --inventory path/to/inventory.json \
  --spec path/to/normalized.json \
  --rehash

python3 .cursor/skills/profile-mcp-sources/scripts/validate_source_profiles.py \
  path/to/source_profiles.json \
  --inventory path/to/inventory.json \
  --spec path/to/normalized.json
```

Omit an unavailable `--inventory` or `--spec`, but supply every artifact used to
derive the source worklist. Do not finish until the second command prints
`OK`.

## Handoff

Report:

- number of candidate, deduplicated, profiled, and skipped sources;
- each skipped URL and reason;
- any fragment rejected after its one retry, with the final field error;
- output path and validator command;
- confirmation that no workbook values were used or emitted.
