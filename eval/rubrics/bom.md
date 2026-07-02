# Bill of Materials quality rubric

## Dimension: scope_fidelity
5 = Every requested component and quantity is represented exactly once, with no unrelated services or silent substitutions.
3 = Core scope is present but some mapping or quantities need clarification.
1 = Material requested components are missing, duplicated, or replaced without explanation.

## Dimension: pricing_integrity
5 = SKUs, metrics, quantities, unit prices, multipliers, extended prices, and totals are transparent and internally consistent.
3 = Math is mostly traceable but some pricing is TBD or explanatory metadata is thin.
1 = Totals cannot be reproduced or pricing appears invented.

## Dimension: assumptions_and_qualification
5 = Region, utilization, HA, hours, exclusions, unpriced items, and non-binding status are explicit and decision-relevant.
3 = Key assumptions exist but important commercial qualifications are easy to miss.
1 = The BOM presents estimates as commitments or hides major assumptions.

## Dimension: spreadsheet_usability
5 = The workbook is cleanly structured, scannable, filterable, and ready for an SE to review with a customer.
3 = The workbook is readable but requires cleanup or interpretation.
1 = The workbook is confusing, malformed, or not actionable.

## Dimension: architecture_decision_support
5 = The BOM makes cost drivers, tradeoffs, gaps, and next sizing decisions immediately clear.
3 = It lists costs but offers limited help interpreting them.
1 = It is a raw parts list with little decision value.
