# Land transport-access source

The current product adopts the checksum-pinned OSRM routing snapshot produced
by the land viewshed research notebook. It covers the modeled H3 R7 land-source
universe and includes road-snap distance plus travel time and distance from the
configured regional city origins.

`LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX` is the product of a 5 km exponential
road-proximity component and a 120 minute exponential minimum-city-travel-time
component. It is a research challenger representing theoretical reachability,
not observed travel, visitation, observer-hours, or legal shore access.

The public OSRM response did not expose a reconstructable graph version. The
adoption stage pins the snapshot and metadata checksums but does not claim that
the routes can be regenerated. Promotion requires a versioned regional routing
graph, validated ferry semantics, actual access destinations, and seasonal road
and closure evidence.
