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
