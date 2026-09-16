# Scientific and input contracts

The per-family `DATA_SOURCES.md`, typed configuration, source code and tests define the
actual contracts. Existing product schemas and scientific formulas are preserved by the
namespace migration; extraction does not promote research products.

| Product | Identity/support | Interpretation |
| --- | --- | --- |
| Calendar | date and jurisdiction/configured period | Calendar context, not measured presence |
| Population | country and H3 cell at declared census vintage | Census allocation/estimated context |
| Access | access-site identity or configured H3 cell | Physical/mapped access, not observer counts |
| AIS | DATE or WEEK_START × H3_INDEX at configured resolution | Coverage-limited vessel activity |
| Ferry | service_date × route_key × source_h3; daily/weekly aggregates | Rider/activity proxy with source availability |
| Static viewability | source_h3 × target_h3 within source type | Terrain kernel × conditional vegetation; distance already integrated |
| Reporting opportunity | configured period × H3_INDEX, immutable generation | Opportunity/proxy; not detection or occurrence probability |

Preserve units, time zones, census vintages, spatial support and source dates. Do not fill
unavailable coverage with zero, resolve duplicates by arbitrary first-row selection, or
shift observations into a requested date range. Human disturbance and reporting opportunity
must remain separate; one does not cancel the other.

External geometry, weather/daylight, access/routing inputs and observational releases must
be provisioned explicitly at configured locations. The read-only release-v1 adapter verifies
identity, unique inventory membership, generation containment and checksums. Its legacy
field names and metadata keys are preserved for compatibility, not evidence of a dependency
on an installed species application. Water observation diagnostics are research consumers
of supplied observations and inherit their coverage restrictions.

Source licenses, attribution, completeness and redistribution restrictions remain in each
source manifest/configuration. AIS archive rights are unresolved in the supplied example;
this migration does not authorize redistribution. No research datasets are bundled.
