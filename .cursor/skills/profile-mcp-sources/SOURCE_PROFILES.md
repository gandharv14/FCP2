# `source_profiles.json` contract

The formal schema is [source_profiles.schema.json](source_profiles.schema.json).
The standard-library validator applies the schema plus URL, hash, access-barrier,
attribution, deduplication, and forbidden-value checks.

## Limits

- At most 3 public page reads per source.
- At most 2 excerpts per source.
- At most 240 Unicode code points per excerpt and 400 in total.
- At most 4 attributions and 24 items in each descriptive list.
- Descriptive strings are source vocabulary, not observed datapoints.

## Shape

```json
{
  "schema_version": "1.0",
  "capture": {
    "created_at": "2026-08-15T22:00:00Z",
    "tool": "profile-mcp-sources",
    "agent_model": "gpt-5.6-sol-high",
    "public_read_limit_per_source": 3,
    "canonicalization": "v1",
    "inventory_sha256": null,
    "spec_sha256": null,
    "profiles_sha256": "64 lowercase hex characters"
  },
  "profiles": [
    {
      "source_id": "example-statistics",
      "source_name": "Example Statistics",
      "canonical_url": "https://statistics.example.org/",
      "status": "profiled",
      "skip_reason": null,
      "capture": {
        "attempted_at": "2026-08-15T22:00:00Z",
        "read_count": 1,
        "http_status": 200,
        "final_url": "https://statistics.example.org/",
        "content_type": "text/html",
        "evidence_sha256": "64 lowercase hex characters"
      },
      "terminology": ["seasonally adjusted"],
      "dataset_names": ["Monthly indicators"],
      "field_conventions": ["period uses YYYY-MM"],
      "document_types": ["dataset landing page"],
      "release_cadence": "monthly calendar release",
      "evidence": {
        "attributions": [
          {
            "attribution_id": "landing",
            "title": "Monthly indicators",
            "publisher": "Example Statistics",
            "url": "https://statistics.example.org/",
            "accessed_at": "2026-08-15T22:00:00Z"
          }
        ],
        "excerpts": [
          {
            "text": "Updated each month after the scheduled release.",
            "attribution_id": "landing"
          }
        ]
      },
      "review": {
        "status": "pending",
        "reviewer": null,
        "reviewed_at": null,
        "notes": null
      }
    }
  ]
}
```

This example is structural only. Do not invent an `.example` URL in real output.

## Status rules

`profiled` means the public page was accessible without a barrier. It requires
at least one attribution and at least one non-empty descriptive field.

`skipped` requires one of the closed `skip_reason` values and empty descriptive
fields/evidence. Its `capture.evidence_sha256` is `null`. A skipped source is
never enriched from snippets, mirrors, caches, or another host.

Only `status: profiled` plus `review.status: accepted` can alter downstream
source rendering. Every other state uses the existing generic behavior.

## Attribution

Attributions identify pages actually read, not search-result snippets. Every
attribution URL and `capture.final_url` must pass the same public-URL gate as the
canonical source. Every excerpt references an existing `attribution_id`.
Paraphrases belong in the descriptive fields and must not be presented as
quotes.

## Hashes

All hashes are lowercase SHA-256 hex.

- `inventory_sha256` and `spec_sha256` hash the exact input file bytes; use
  `null` when that artifact was not supplied.
- `capture.evidence_sha256` hashes the UTF-8 canonical JSON serialization of
  that profile's `evidence` object.
- `profiles_sha256` hashes the UTF-8 canonical JSON serialization of the root
  `profiles` array.

Canonical JSON is:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

The validator's `--rehash` mode computes these fields. Normal validation
recomputes and compares them.

## Workbook-value exclusion

Profiles describe how a source names and publishes information. They do not
hold values. The validator rejects:

- keys commonly used for values or workbook/cell content;
- workbook cell-reference syntax in descriptive text;
- scalar values extracted from supplied inventory/spec value-bearing keys when
  those values occur in profile descriptions or excerpts.

Value-bearing keys include `value`, `values`, `raw_value`, `raw_values`,
`workbook_value`, `workbook_values`, `cached_value`, `cached_values`,
`display_value`, `display_values`, and `forbidden_values`. The check recursively
walks JSON inventory/spec files and supports JSONL inputs.

This leakage check is a guardrail, not evidence reconciliation. If source prose
conflicts with the workbook, retain the workbook value and use the profile only
for vocabulary and document shape.
