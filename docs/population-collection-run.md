> Storage update 2026-10-17: this is historical numerical review evidence. Current products are the three [final-only Parquets](final-delivery.md). The old `Data/Human/.static-work`, `provenance` and `2009-forward` locations have moved to toolkit `data/static-work`, `data/provenance` and `data/retained/study-2009-forward`. Exact original hashes and recovery paths are retained in the final-layout ledger.

Historical 2026-10-16 run. The current product includes the 2026-10-17 independent
review corrections described in [static delivery](static-delivery.md#independent-review-corrections-2026-10-17).
The acquisition-coverage checks below remain evidence for the unchanged footprint;
its quarantine/count and approximation results describe the earlier product.

# Executed radius-aware population collection

The full current-registry run completed and was validated before replacing the local
Human product. The displayed registry is unchanged: **13,461 exact H3 R7 cells**.
The collection support is the union of every full cell and its 100 km WGS84 center
neighborhood, with 50 m of acquisition-only approximation margin. Scientific
25/50/100 km radii and original census-polygon denominators remain unchanged.

The collection footprint covers 328,771.057 km². Its conservative WGS84 acquisition
envelope is west −130.839400, south 45.109145, east −120.826751, north 52.139835.
Every cell polygon and 215,376 true 100 km boundary samples were verified inside it.
All required jurisdictions are supplied: BC, WA and OR. The normalizer selected
148,831 complete original polygons from existing official US 2020 / Canada 2021
archives, without downloads or raw-source copies. These source files already covered
the footprint: the preceding preparation had used a broader 250 km bounding envelope.
There remains no 250 km public metric.

## Full-run result

| Canonical metric | Usable before | Usable after | Still null |
|---|---:|---:|---:|
| Per-cell count | 9,331 | 9,331 | 4,130 |
| 25 km | 3,300 | 3,300 | 10,161 |
| 50 km | 2,657 | 2,657 | 10,804 |
| 100 km | 438 | 438 | 13,023 |

**No coverage status changed.** Acquisition width was already sufficient; explicit
planning now makes that testable. Residual geographic support includes 1,303.137 km²
inside the pinned land reference but outside census coverage, and 44,216.330 km²
outside the supplied land/water context. These are geographic areas, never population
coverage percentages. Neither gap is silently converted to zero. The context and
shared study config were not expanded or edited.

The narrower source-selection envelope retains 19 originally unavailable Canadian
count records (23 existed in the previous wider input inventory) and 46 quarantined
border records (previously 77). No retained previously quarantined record was
unquarantined. Different inventory counts reflect selection, not recovered census
values. The audit identifies 51 unknown-count source IDs intersecting the acquisition
union, including its safety margin. Per-output unknown-source and geographic flags
remain explicit. The derived collected 100 km estimate is still populated for all
cells, with 13,023 missing-as-zero flags and 438 complete-source rows.

Changing the normalized source envelope also changes the recorded regional equal-area
projection. The largest subtotal difference is 0.290305 person at 100 km; differences
are below one person and no source values were rounded or rewritten. Direct original-
source intersection checks pass for the new projection. Calendar file bytes and all
retained infrastructure values are unchanged.

## Verification and local artifacts

- 484 native tests passed; GDAL and OSRM runtime preflights passed.
- Regular wheel installed outside the checkout with application imports blocked;
  resource/init/27 family-help checks and 70 installed-wheel tests passed.
- Full 13,461-cell population recomputation plus calendar readback, exact keys,
  metric/status/null/finite/cumulative checks, 24 direct original-source intersection
  comparisons and 12 source-conservation checks passed.
- The completed calculation/delivery pass took 488.67 s after source preparation;
  process peak RSS was 3,305,029,632 bytes. Scratch/output was about 305 MiB.
- Human-only app cache refreshed from the validated source: all 27 exported columns
  match exactly, all 1,473 combined referenced assets hash verified, and other-domain
  features/product bindings plus Orca/Hydrology catalog bytes are unchanged.
- No provider downloads, raw-source mutations, shared registry changes, push, merge,
  deployment or duplicate current final release.

Current product: `Data/Human/catalog.json` from the MarineCast workspace root.
Evidence: `Data/Human/provenance/radius-aware-collection/` contains the bound footprint
plan, collection audit, independent validation, full-run report, code hashes and exact
run/audit scripts. Normalized source and calculation caches live under
`Data/Human/.static-work/radius-aware-100km/`; a marked rollback of the previous
current files is under the evidence directory. Older `static-completion` evidence is
historical and retained unchanged.

See [collection workflow](population-collection.md) for commands. Genuine count gaps
and physical/census boundary disagreements still need source-specific resolution;
expanding acquisition alone does not certify their population totals.
