# Repeated | € label

- Workbook: `runtime`
- Output band: `1 O'Brien!A7` on sheet `1 O'Brien`
- Derivation: 2 line items (0 inputs, 2 intermediate)
- Longest dependency chain: 6

## Values

| cell | recomputed | workbook |
| --- | --- | --- |
| `1 O'Brien!A7` | 7 | saved |

## Derivation (line-item level)

| # | bucket | line item | sheet | span | formula | depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A2` | `=$A$1+1` | - |
| 1 | middle | Repeated | € label | 1 O'Brien | `1 O'Brien!A7` | `=$A$1+1` | `1 O'Brien!A2` |

## Derivation (cell level)

### `1 O'Brien!A7`

1 ancestor cells

| # | role | cell | label | formula | value |
| --- | --- | --- | --- | --- | --- |
| 0 | middle | `1 O'Brien!A2` | Δ / Unicode | `=$A$1+1` | False |
| 1 | output | `1 O'Brien!A7` | Δ / Unicode | `=$A$1+1` | 7 |
