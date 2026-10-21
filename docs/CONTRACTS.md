# Scientific and input contracts

The per-family `DATA_SOURCES.md`, typed configuration, source code and tests define the
actual contracts. Existing product schemas and scientific formulas are preserved by the
namespace migration; extraction does not promote research products.

Human is data-only. Human layers and source-supported pressure, exposure and effects
datasets retain their source meaning, measurement or derivation method, units,
spatial/temporal support and uncertainty. Activity and access data alone do not
establish a causal ecological effect. Ecological response fitting, prediction and
observation/reporting-process assumptions belong to consuming modeling work.

| Product | Identity/support | Interpretation |
| --- | --- | --- |
| Calendar | date and jurisdiction/configured period | Calendar context, not measured presence |
| Population | country and H3 cell at declared census vintage | Census allocation/estimated context |
| Access | access-site identity or configured H3 cell | Physical/mapped access, not observer counts |
| Ports | `nga_wpi:<portNumber>`; original EPSG:4326 point plus containing-R7 recorded source count | Physical reference inventory; source harbor/facility codes retained, no legal authority or vessel activity |
| Ferry | service_date × route_key × source_h3; daily/weekly aggregates | Rider/activity proxy with source availability |

Observation/reporting-opportunity combinations are modeling-owned, excluded from
active Human products. Their old land/water/observer namespaces, templates and
artifacts remain compatibility-only; [product scope](product-scope.md) defines this
boundary. The legacy static-viewability API preserves source/target H3 identity and
its terrain/vegetation contract without claiming independent Viewshed parity.
Atmospheric visibility is not a Human source-data completion gate.

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
source manifest/configuration. AIS acquisition/aggregation is owned by toolkit-ais, not Human. Retained legacy
research consumers may read explicitly supplied historical AIS inputs; no acquisition
or redistribution is implied. No research datasets are bundled.

The independent [static population v1 contract](population.md) uses original census
polygons for full-cell and geodesic neighborhood area allocation. It preserves missing
source geometry, mixed census vintages and per-metric completeness. Legacy water-distance
weights and population-context schemas retain their original meaning.

The shared-study static population delivery may qualify census-free geographic gaps
using the checksum-pinned physical-water context minus pinned physical land from the
validated registry manifest. The explicit assumption is zero resident-population
support over that supplied static cartographic water only. This never replaces a
census allocation, suppresses an unknown count, extrapolates a mask, or certifies an
uncovered land area. The standalone population API remains conservative unless its
caller explicitly supplies complete geographic support. See static-delivery.md.

Radius-aware population source preparation materializes full target-cell and largest
WGS84 neighborhood support before reading census inputs. The bound footprint includes
a declared acquisition-only geometric margin; original scientific radii and complete
original census polygon areas stay unchanged. Prepared sources bind the exact target
set/radii, and delivery rejects mismatches. Acquisition extent completeness is distinct
from source-value/geographic completeness. See [collection contract](population-collection.md).

The explicit distinct-national-count overlap policy preserves published US/Canada
counts and original full polygon denominators despite recorded cross-provider
cartographic overlaps. It still rejects duplicate IDs and same-country overlaps.
This is an alternative to whole-source quarantine, not a claim of matched boundary
position. Shared-study collected estimates now cover cell count, full-cell density,
and 25/50/100 km radii under the same documented missing-contribution policy;
raw missing counts and all coverage evidence are preserved. Shared-study public delivery exposes only collected count, density and 25/50/100 km quantities; strict duplicate scalar fields remain internal validation results. The machine identifiers keep `_collected`; the display labels are Population Count, Population Density (people/km²), and Population Within 25km/50km/100km, all in the Population family. Source-subtotal and missingness qualifiers are retained. See population.md.

Native geometry exports identify category and retain original source attributes; they do not derive or publish an `access_state` variable or access-state summaries. Category colors and selection are independent of access evidence. Existing source access attributes remain provenance, not a new display variable.
