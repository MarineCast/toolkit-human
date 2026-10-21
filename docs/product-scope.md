# Supported Human product

The supported outcome is exactly `static.parquet`, `dynamic-calendar.parquet` and `static-native.parquet`, produced by `human.get_human_data`. It includes collected resident population, source-derived infrastructure summaries, jurisdiction/local-date calendar context, native launches, shore sites, places, shoreline classifications and park context. See [the pipeline](get-human-data.md).

The catalog and CLI advertise only this product. Existing direct family helpers, ferry, viewshed, routing, old population surfaces, calendar-effort and reporting-opportunity code/configuration remain legacy compatibility. They are not pipeline stages or additional supported deliveries. AIS is owned by toolkit-ais. New effects/pressure/tourism/fishing products and the distance-to-features TODO are deferred.

Physical access, mapped inventories, resident population, observed presence, visitation, reporting, detection and occurrence remain distinct. Preserve source dates, partial coverage, unknowns and zero-imputation qualifiers. Native classifications are evidence rather than guaranteed present legal access. The three unqualified shoreline length/fraction/state fields remain audit-only and withheld from selection.

Physical port facilities are Human-owned under the user-approved 2026-10-19 extension. NGA source points, IDs, names, harbor/facility codes and provenance are retained independently of marina destinations; AIS may join the stable port IDs for vessel activity. Governance retains regulatory/authority boundaries. No distance metric was added. See [physical ports](ports.md).
