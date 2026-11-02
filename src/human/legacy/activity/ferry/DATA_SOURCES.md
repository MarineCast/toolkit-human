# Ferry data sources

Washington rider records come from the WSF Tableau Public ridership workbook.
BC rider records are estimated daily/hourly allocations of official monthly
route totals and remain labeled estimated. Route geometry is an explicit
WSDOT/BC Ferries crosswalk. WSDOT vessel history improves platform-duration
estimates where available; configured route durations remain explicit
fallbacks elsewhere. Strict publication requires complete enabled-route,
ridership-period, and vessel-history coverage.

WSDOT history normalization retains UTC epoch instants alongside local display clocks.
Elapsed voyage duration uses UTC to handle daylight-saving transitions, and repeated
local-hour sailings remain distinct by UTC identity. The arrival is still estimated,
not an observed arrival. This does not supply actual timestamps for WSF Tableau's
scheduled-departure ridership or BC's monthly-derived hour buckets; see
`docs/release-readiness.md` for daily-delivery limitations.

## Hyper diagnostic cleanup

The legacy WSF reader gives each Hyper process a unique temporary
`hyper-logs-*` directory inside the configured ferry cache. After the connection
and process close, that directory and its diagnostics are removed on success
or failure. This prevents new `hyperd.log` files in the working directory.
Existing logs, cached TWBX/Hyper source files, Parquet outputs and provenance
are preserved; no historical log sweep is performed.
