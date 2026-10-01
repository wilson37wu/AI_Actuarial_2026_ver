# Assumption Register

This file tracks external assumption sources and their evolution over time.

## Mortality Assumption (HK)

- **Assumption name:** Hong Kong mortality table (attained-age, male, female)
- **Primary source link:** https://www.actuaries.org.hk/storage/download/Asian%20mortality%20table%20archive.xls
- **Model usage:** `par_model_v2/liabilities/stochastic_participating.py` via `HKMortalityTable`
- **Expected CSV schema for runtime input:**
  - `attained_age`
  - `male`
  - `female`
- **Selection factors applied in model:**
  - Year 1: 70%
  - Year 2: 90%
  - Year 3+: 100%

## Evolution Log

| Date (UTC) | Version tag | Change summary | Owner/Notes |
|---|---|---|---|
| 2026-02-15 | HK-MORT-v1 | Registered external source link and model schema contract for user-supplied CSV. | Initial baseline; update this row when source/extraction/process changes. |

## Governance Notes

- Keep this file updated whenever mortality source, transformation, or selection treatment changes.
- If a newer official table is adopted, add a new evolution row and preserve historical rows for auditability.
- Runtime model intentionally consumes user-provided CSV so table updates can be applied without code changes.
