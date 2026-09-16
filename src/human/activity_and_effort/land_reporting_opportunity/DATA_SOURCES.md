# Land reporting opportunity

This research product estimates relative opportunities for a land observer to
see and report whales. It is not observed visitation, observer-hours, detection
probability, vessel pressure, or whale truth.

The primary daily composite is built at target H3 R6 as:

`physical viewshed and viewing conditions x reachable population/travel x road
proximity x mapped public-shore evidence x calendar opportunity`.

Physical conditions combine the static land viewshed with distance-aware
visibility, daylight, wind, and precipitation. Population travel, roads, city
access, combined transport access, broad reachability, mapped-public access,
and verified-public access are also published as standalone streams. The model
can therefore use the composite and its components independently and can test
ablations without rebuilding the source data.

The mapped-access composite is the primary research proxy. It includes
authoritative verified-public sites and OSM features explicitly tagged as
public. The verified-only composite is a stricter sensitivity lower bound.
Unknown access is omitted from these clearly labeled lower-bound sums and its
context fraction is published; it is never converted to zero. Broad
reachability remains available separately so incomplete access mapping does not
erase all theoretical opportunity.

Daily and weekly products retain fixed-reference q99 indices for inspection.
Models consume raw opportunity values with training-fold-fitted transformations,
not those indices. Sightings do not construct or scale the canonical proxy.
Partial access coverage is acceptable in explicitly labeled modeling research;
it is not necessary to finish a complete BC inventory before evaluating it.

The active `manifest.json` resolves one immutable generation containing source,
target, daily, weekly and metadata artifacts. Flat legacy paths are preserved
snapshots, not current-product aliases. Consumers must resolve the manifest or
use `load_land_reporting_config()`/`modeling.load_weekly()`.

`modeling.py` supplies exact previous-week and calendar 4-/13-week windows,
explicit coverage blocks and cell-level/regional adapters. `experiment.py`
compares baseline, coverage, composite, components and combined formulations.
`health.py` checks generation identity, input checksums, freshness and support.
Regional absence scoring requires verified sightings coverage; positive-event
allocation can proceed without inventing non-report labels.

Build and inspect with:

```bash
python -m human.activity_and_effort.land_reporting_opportunity.download --overwrite
python -m human.activity_and_effort.land_reporting_opportunity.build --allow-partial --overwrite
python -m human.activity_and_effort.land_reporting_opportunity.inspect
```

The retrospective evaluator is downstream only:

```bash
python -m human.activity_and_effort.land_reporting_opportunity.evaluate --overwrite
```

Report-free cells mean no public-release-eligible canonical report in the
evaluation window, not confirmed whale absence.
