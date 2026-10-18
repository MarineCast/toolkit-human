# Population data sources

The United States pipeline uses 2020 Decennial PL block population and TIGER
block/state geography from the United States Census Bureau. The Canadian
pipeline uses 2021 dissemination-area geography and population from Statistics
Canada. Country products preserve their original census vintages; the
cross-border table harmonizes column semantics without pretending the vintages
are contemporaneous.

The Canadian downloader caches Statistics Canada's official comprehensive 2021
Census Profile CSV archive for British Columbia, streams only dissemination-area
`Population, 2021` rows, and writes a compact Parquet cache. The published
archive contains 28 DA rows whose population value is blank. Those identifiers
are retained in `bc_da_population_2021_unavailable.parquet` with
`measurement_status=unavailable` and
`qc_reason=STATCAN_POPULATION_VALUE_UNAVAILABLE`. If an unavailable DA intersects
the configured allocation domain, it is excluded explicitly and is never
treated as zero.

`population_context_h3_r7.parquet` collapses the country-qualified rows to one
row per H3 cell for safe downstream joins. Border cells retain nullable U.S. and
Canadian population components, country/subdivision codes, and mixed-vintage QC.
The context adds only `log1p` population; it does not apply public-access,
travel-time, viewshed, observer-effort, or marine-cell transfer weights. An
absent country component remains null rather than becoming an observed zero.
