# Boat-launch access sources

## Washington Department of Ecology public access points

The statewide marine public-access point layer supplies named public sites and
the `Boat_Launch` amenity field. It supports observed public status for selected
launch records. The source categories `Motorized`, `Non-motorized`, and `Both`
are retained as capability states; a literal `Yes` remains
`present_type_unknown`. Seasonal operation remains `unknown` when not reported.

## DataBC Coastal BC Boat Launches

The Province of British Columbia publishes a coastal launch location inventory
under the Open Government Licence - British Columbia. Its metadata describes a
circa-2004 legacy dataset, cautions that it may be incomplete or inaccurate, and
states that no updates are planned. It supports observed launch locations, not
public status, ramp capability, current operation, or complete coverage.

## OpenStreetMap slipways

OSM `leisure=slipway` and `man_made=boat_ramp` objects are acquired through
Overpass under ODbL 1.0. They remain source-labeled, community-mapped
supplemental evidence. Missing `access` and `opening_hours` tags stay unknown;
OSM coverage never upgrades the collection to complete.

OSM is an optional supplement operationally. Acquisition uses bounded tiles and
configured Overpass mirrors. If every mirror fails for any tile, the complete
OSM request is recorded as `unavailable`; an empty checksum-pinned snapshot is
published as outage evidence and is never interpreted as an observed zero.

Agency and OSM rows are not yet collapsed into unique physical launches. The
source-record count therefore must not be interpreted as a deduplicated launch
count until the cross-source matching QC task in the root roadmap is complete.

## Output semantics

Only occupied H3 R7 cells are published. A cell absent from the output is
unknown, not a zero. The collection is research-only until authoritative,
current WA/BC public status, capability, seasonal operation, and completeness
requirements are met.
