# Public shoreline-access sources

## Washington Department of Ecology public access

The Washington marine shoreline public-access line layer provides the mapped
length of publicly accessible waterfront. The companion point layer provides
named access locations and facility attributes. The adopted marine shoreline
layer supplies a separate total-shoreline denominator. The H3 R7 fraction is
calculated from dissolved projected line intersections; point records never
stand in for accessible length.

## British Columbia recreation-site and shoreline evidence

The pipeline acquires the official Recreation Sites Subset from Recreation
Sites and Trails B.C. through DataBC WFS. It includes access descriptions,
activities, structures, directions, and reported closures. The official B.C.
ShoreZone line layer is acquired separately. A recreation site within the
configured shoreline distance is an authoritative facility candidate, not
verified public shore access. A missing closure does not prove legal or
traversable shore access. Recorded closures remain restricted evidence.

These sources remain partial. Recreation sites are not a complete inventory of
municipal beaches, parks, trailheads, rights-of-way, or other legal shore
entrances. ShoreZone is a shoreline denominator and coastal-connectivity check;
it does not establish public access. No B.C. accessible-shoreline line numerator
is currently available, so B.C. waterfront fractions remain null.

## OpenStreetMap supplemental access

OSM water-access points, beaches, beach resorts, slipways, trailheads, and
viewpoints are acquired through Overpass under ODbL 1.0. A feature is labeled
public only when an explicit public/permissive pedestrian access tag supports
that state. An explicit `foot` tag takes precedence over general `access`.
Private or prohibited access is retained as restricted, and missing tags remain
unknown. Conditional access tags remain conditional rather than being promoted
to public. Opening hours, operator, ownership, and parking tags are retained as
supporting evidence. Representative OSM points can extend facility evidence but are never
used as the authoritative waterfront numerator or denominator. Generic piers
are intentionally excluded because OSM commonly represents one physical pier
with multiple line objects and pier presence does not imply legal shore access.
OSM representative points must also fall within the configured distance of the
official WA or B.C. shoreline reference. This rejects inland viewpoints and
trailheads without claiming that shoreline proximity proves legal access.

OSM is an optional supplement operationally. Acquisition uses bounded tiles and
configured Overpass mirrors. If every mirror fails for any tile, the complete
OSM request is recorded as `unavailable`; an empty checksum-pinned snapshot is
published as outage evidence and is never interpreted as an observed zero.

Agency and OSM facility rows remain distinct until a cross-source matching rule
has been validated. Facility counts are source-record counts, not guaranteed
counts of unique physical access sites.

## Output semantics

Because source coverage is partial, a shoreline cell without mapped public
access has null `PUBLIC_ACCESSIBLE_SHORELINE_M` and null
`ACCESSIBLE_WATERFRONT_FRACTION`, not zero. The collection remains
research-only pending a B.C. access-boundary source and a WA/BC completeness
review. `PUBLIC_ACCESS_EVIDENCE_STATE` distinguishes authoritative verified
points, explicitly public OSM records, access-unknown mapped candidates,
restricted records, and cells with no evidence.

The access and total-shoreline layers do not align perfectly at H3 boundaries.
When accessible length exceeds the mapped total length in a cell, the pipeline
keeps the access state, publishes the raw ratio as QC evidence, flags a
denominator mismatch, and leaves the fraction null instead of clipping it to
one.
