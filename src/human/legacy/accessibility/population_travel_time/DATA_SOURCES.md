# Population travel-time source

The current product adopts the checksum-pinned OSRM population-routing snapshot
produced by the land viewshed research notebook. The snapshot uses selected H3
R4 population origins representing at least 99 percent of supported population,
plus nearby low-population origins, and routes them to modeled H3 R7 land-source
centroids.

The output publishes 60, 120, and 240 minute exponential travel-demand
components. `POPULATION_TRAVEL_OPPORTUNITY_INDEX` uses the 120 minute variant
after log1p transformation and a source-universe 99th-percentile cap. It remains
a research challenger, not observed visitation, trips, or observer effort.

Promotion requires a versioned routing graph, validated ferry and seasonal
semantics, destinations at real public-access entrances, and calibration or
sensitivity evidence for the origin selection, decay scales, and cap.
