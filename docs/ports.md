# Physical port inventory

Human owns physical port facilities; Governance owns regulatory/authority boundaries and AIS owns vessel activity. The approved 2026-10-19 migration uses the already-retained official NGA World Port Index response from 2026-10-14. It makes no source requests.

The full original GeoParquet contains 2,951 source points and all original attributes. The default `native.ports` recipe pins its SHA256 and the 84 source IDs already delivered by Governance at R6/R8. The canonical ignored input is `data/sources/2026-10-14/ports/geometry.parquet`. Its embedded original provenance retains the response hash, DMS conversion, source URL, source/retrieval dates, attribution and rights. Historical Governance paths in that provenance describe ancestry; the new recipe reads Human's source store. There is no Governance package/application dependency or automatic acquisition fallback. A missing or changed source fails.

`portNumber` remains the source identity. `record_id` and `PORT_ID` are `nga_wpi:<portNumber>`; native `feature_id` is `ports:nga_wpi:<portNumber>`. Original `globalId`, `unloCode`, names, coordinates, `harborType`, `harborUse` and all other source fields remain attributes without inferred legal interpretation. `FACILITY_TYPE` is `published_port_location_point`. Unknown codes and nulls are preserved. This inventory is not labeled as exclusively commercial ports. Marinas remain separate destinations; a shared name or nearby/coincident geometry never merges distinct facilities. Exact source identities are reconciled once. The retained comparison found 17 port names shared with other Human source classes and five point pairs within 100 m, including one marina pair. These are ambiguous comparison candidates, not supported physical-identity crosswalks; they remain separate records. The comparison distances are QA evidence only, not delivered distance metrics. No distance metric is introduced.

After the 2026-10-24 review, native port geometry stays in the pinned original store and is no longer exported in `static-native.parquet`. All 84 selected source IDs remain retained; seventy lie in the Human R7 reporting cells and fourteen outside those cells contribute no reporting-cell count. No source point is relocated to an H3 center.

The retained `nga-physical-port-ownership-1` evidence has three fields. Only integer 0/1 `has_port` remains in the 14-column public static table; original counts, status and completeness arrays remain in its external audit:

| Column | Meaning |
| --- | --- |
| `has_port` | Public int64 0/1 indicator: retained source count is positive after null-to-zero encoding |
| `ports__SOURCE_RECORD_COUNT` | Audited original integer count of distinct retained NGA IDs in the containing cell |
| `ports__MEASUREMENT_STATUS` | `partial_published_port_inventory` |
| `ports__SOURCE_COVERAGE_COMPLETE` | `false`; no claim of exhaustive facilities |

Metadata retains the selected source-ID to R7-cell assignments so validators can check counts without a delivered native point file. The 13,461 existing cells and their order are preserved. Original audited counts sum to 70, with a maximum of one point per occupied cell in this snapshot; public `has_port` equals 1 in those 70 cells. Zero means no recorded point from this retained inventory in that cell, not absence of physical ports. Counts are not polygon coverage, marina counts, visits, activity, legal jurisdiction or navigation advice. Ports have no date-series expansion. Population/calendar values are unchanged. The later public consolidation fills infrastructure nulls as zero while preserving original values and masks in audit evidence. The preceding 64/67-column source schemas remain reconstructible from compact static plus its external audit. Current static v6 always has 14 columns; an unprovisioned port source encodes 0 by explicit user instruction, with original unknown support in the audit.

The retained rights state local reference use only, with NGA MSI legal/commercial-use terms and redistribution rights not independently cleared. No redistribution clearance is inferred from this ownership change.

## Reproduction and narrow migration

Normal `human.get_human_data()` production reads `native.ports` and appends the R7 summary and source provenance. It supports both source-adapter collection and explicit retained native specifications. This follows the caller's ordinary full pipeline semantics, including its separately configured source collection.

To extend an existing verified Human delivery without rebuilding population, calendar or other facilities:

```python
from pathlib import Path
import yaml
from human.pipelines.native.ports import migrate_delivery, resolve_entry

workspace = Path('/path/to/toolkit-human')
recipe = yaml.safe_load((workspace/'config/human.yaml').read_text())
migrate_delivery(Path('/path/to/Data/Human'),
                 resolve_entry(recipe['native']['ports'], workspace),
                 metadata_dir=workspace/'data/delivery/new-immutable-port-receipt')
```

This obtains the same Human publication lock, stages on the output filesystem, verifies every previous non-port Arrow value/type/null and validates the static source counts and two-product contract, and restores the prior directory if final readback fails. Pre-existing port summary provenance causes an explicit reconciliation error. Original inputs and prior external receipts remain recoverable. Successful temporary staging/backup directories are cleaned; delivery remains exactly two Parquets with no replay directory.

Current migration evidence is retained outside delivery in `data/provenance/ports-migration-20261019/selection.json` and `validation.json`; the external receipt is `data/delivery/ports-migration-20261019/catalog.json`. Evidence records source/grid/product hashes, explicit ID selection, runtime versions and semantic parity. The port tests cover same-coordinate distinct facilities, unknown attributes, checksum/selection rejection, outside-grid original-source retention, offline pipeline inclusion, exact existing-value preservation and publication rollback. These are software/product checks, not source completeness or current facility operation certification.

Historical 2026-10-19 evidence (before native output removal): the live-reader check passed over loopback HTTP: Human offers Port Locations and Port Source Records, Governance offers no port fields, all 84 native point features and attributes match Human exactly, and all 13,461 exported R7 port-count values match the static product (sum 70). The historical public startup seed remains unchanged; the live reader rejects its old source bindings and regenerates changed domains. See `data/provenance/ports-migration-20261019/consumer.json` and [shared explorer QA](https://github.com/MarineCast/applications-data-explorer/blob/b570ace8b05068482ed81868245c634fc1574278/docs/qa/ports.md). Human source and regular-wheel supported suites each passed 144 tests with one legacy test excluded. Shared-owner frontend/DOM tests, Python readers, typecheck, lint and production build passed. No interactive browser QA was taken.

Public static v6 names the integer presence field `has_port`; raw source/audit builders keep `ports__SOURCE_RECORD_COUNT`. See the [name mapping](final-delivery.md#column-names).
