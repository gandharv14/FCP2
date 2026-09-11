# Repeated | € label

- Workbook: `fixture`
- Output band: `1 O'Brien!A7` on sheet `1 O'Brien`
- Derivation: 7 line items (1 inputs, 6 intermediate)
- Longest dependency chain: 6

## Values

| cell | recomputed | workbook |
| --- | --- | --- |
| `1 O'Brien!A7` | 7 | saved |

## Derivation (line-item level)

| # | bucket | line item | sheet | span | formula | depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | input | Repeated | € label | 1 O'Brien | `1 O'Brien!A1` | `=$A$1+1` | - |
| 1 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A2` | `=$A$1+1` | `1 O'Brien!A1` |
| 2 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A3` | `=$A$1+1` | `1 O'Brien!A2` |
| 3 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A4` | `=$A$1+1` | `1 O'Brien!A3` |
| 4 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A5` | `=$A$1+1` | `1 O'Brien!A4` |
| 5 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A6` | `=$A$1+1` | `1 O'Brien!A5` |
| 6 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A7` | `=$A$1+1` | `1 O'Brien!A6` |

## Derivation (cell level)

### `1 O'Brien!A7`

6 ancestor cells, 3 traced (budget dropped 4)

| # | role | cell | label | formula | value |
| --- | --- | --- | --- | --- | --- |
| 0 | input | `1 O'Brien!A1` | Δ / Unicode | `=$A$1+1` |  |
| 1 | middle | `1 O'Brien!A6` | Δ / Unicode | `=$A$1+1` | inf |
| 2 | output | `1 O'Brien!A7` | Δ / Unicode | `=$A$1+1` | 7 |
