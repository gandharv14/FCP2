# Repeated | € label

- Workbook: `fixture`
- Output band: `1 O'Brien!A5` on sheet `1 O'Brien`
- Derivation: 5 line items (1 inputs, 4 intermediate)
- Longest dependency chain: 4

## Values

| cell | recomputed | workbook |
| --- | --- | --- |
| `1 O'Brien!A5` | nan | saved |

## Derivation (line-item level)

| # | bucket | line item | sheet | span | formula | depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | input | Repeated | € label | 1 O'Brien | `1 O'Brien!A1` | `=$A$1+1` | - |
| 1 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A2` | `=$A$1+1` | `1 O'Brien!A1` |
| 2 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A3` | `=$A$1+1` | `1 O'Brien!A2` |
| 3 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A4` | `=$A$1+1` | `1 O'Brien!A3` |
| 4 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A5` | `=$A$1+1` | `1 O'Brien!A4` |

## Derivation (cell level)

### `1 O'Brien!A5`

4 ancestor cells, 3 traced (budget dropped 2)

| # | role | cell | label | formula | value |
| --- | --- | --- | --- | --- | --- |
| 0 | input | `1 O'Brien!A1` | Δ / Unicode | `=$A$1+1` |  |
| 1 | middle | `1 O'Brien!A4` | Δ / Unicode | `=$A$1+1` | 1.5 |
| 2 | output | `1 O'Brien!A5` | Δ / Unicode | `=$A$1+1` | nan |
