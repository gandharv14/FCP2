# Repeated | € label

- Workbook: `runtime`
- Output band: `1 O'Brien!A5` on sheet `1 O'Brien`
- Derivation: 2 line items (1 inputs, 1 intermediate)
- Longest dependency chain: 4

## Values

| cell | recomputed | workbook |
| --- | --- | --- |
| `1 O'Brien!A5` | nan | saved |

## Derivation (line-item level)

| # | bucket | line item | sheet | span | formula | depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | input | Repeated | € label | 1 O'Brien | `1 O'Brien!A1` | `=$A$1+1` | - |
| 1 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A5` | `=$A$1+1` | `1 O'Brien!A1` |

## Derivation (cell level)

### `1 O'Brien!A5`

1 ancestor cells

| # | role | cell | label | formula | value |
| --- | --- | --- | --- | --- | --- |
| 0 | input | `1 O'Brien!A1` | Δ / Unicode | `=$A$1+1` |  |
| 1 | output | `1 O'Brien!A5` | Δ / Unicode | `=$A$1+1` | nan |
