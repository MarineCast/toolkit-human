# UTC delivery evidence and bounded schedule pilot

This is a method-review candidate, not a replacement for the immutable native test
release or a qualified Human application product. No new downloads or AIS acquisition
are required by the pilot. Broader publication requires independent review.

## Supported conversion

`human.activity_and_effort.ferry.utc_schedule.summarize_utc_schedule(frame)` accepts
normalized WSF records and returns a route/direction UTC-day table plus an unresolved
time ledger. Inputs must have unique nonempty sailing identities and explicit
`departure_time_source == scheduled`. Local naive timestamps mean America/Los_Angeles.
Ambiguous/nonexistent/invalid timestamps remain in the ledger; no fold or clock repair
is inferred. No records are silently discarded. The caller must preserve both outputs,
the supplied source snapshot/hash, original date limits and this method definition.

The key is `(segment_id, direction_id, valid_start_utc)`. Boundaries are aware UTC
midnight inclusive to next midnight exclusive; `date_utc` labels the interval start.
Directions remain separate. Vessel identities are deliberately aggregated as distinct
source records, not deduplicated physical trips. There is no H3 assignment.

| Metric | Units and meaning | Status/coverage |
| --- | --- | --- |
| `utc_resolved_schedule_record_count` | Count of supplied retained records whose scheduled UTC instant resolves into this interval | `observed` means exact count of this defined subset, not actual trips or complete schedules |
| `reported_total_at_scheduled_departure_sum` | Sum of the normalized source's reported rider totals attributed to scheduled departure date; not unique people or exposure | `observed` if all included records have values, `partial` if some do, `unavailable`/null if none do |
| `reported_total_known_record_count` | Included records with a non-null total | Denominator diagnostic |
| `reported_total_record_coverage_fraction` | Non-null totals / included UTC-resolved records | Source-value completeness only; excludes unresolved-time records |
| `regional_traffic_coverage_fraction` | Unknown real-world coverage | Always null with `unknown` status |

All-null sums remain null and explicit zeros remain zero. Absent route/day groups
are not emitted as zero. The source normalization uses the workbook total-rider field
or its existing vehicle/vehicle-passenger/walk-on fallback; this pilot does not revise
that definition. Source filtering, schedule timing, archive edges and unresolved-time
records limit the subset. The result must not be named total UTC traffic, rider-hours,
vessel-hours or observed departure counts. Neither BC allocations nor one-day WSDOT
history is mixed into this product.

## Actual retained evidence, 2026-10-05

- WSF refreshed source: 675,532 records through August 2026, unique sailing IDs;
  scheduled rather than actual times, with 20 ambiguous/nonexistent local timestamps.
- Small actual pilot: local windows March 7–10 and October 31–November 3, 2020,
  and July 19–22, 2025. Of 3,582 records, 3,579 resolve; 3 remain in the ledger.
  Output has 284 route/direction UTC-day rows. Record counts and reported sums are
  conserved across the output and ledger. No regional rebuild or publication occurs.
- HRRR inventory: 14,574 timestamped sample files, all present (424,631,251 bytes),
  local dates 2020-01-01 through 2026-08-25. This is file-presence inventory, not a
  fresh checksum verification of the entire archive. UTC bins contain six samples
  on 2,415 days, seven on 7 days, five on 6 days, four on 1 day and one on 1 day.
- Weather pilot: all six source hashes for UTC 2025-07-20 verified; 499 R5 cells,
  samples at 03/07/11/15/19/23 UTC. Arithmetic visibility and wind sample means are
  possible; they are not continuous-time daily means or new R7 observations.

## Land method and remaining evidence

The current weather reducer requires six samples per cell/day. Wind and visibility
are arithmetic snapshot means; precipitation is explicitly the sum of six forecast
rates multiplied by four hours, not observed hourly accumulation. Re-binning by UTC
requires a declared policy for DST/edge bins and sample coverage. Simply demanding
six samples or calling each rate four hours of measured precipitation is insufficient.
The existing daylight formula computes seasonal day length from latitude and day of
year; changing a timezone label does not integrate daylight over a UTC interval.
Calendar context represents local jurisdictional days. Nonlinear condition weights
and their product with calendar/daylight must be defined before aggregation: a
product of daily means generally differs from a mean of time-specific products.

Proposed review sequence:

1. Retain UTC-bin *sample means* as separate R5 diagnostics, including valid sample
   counts/times, phase and source availability. Do not call these full-day integrals.
2. Specify UTC daylight integration from solar geometry at native centroids and
   validate polar/leap/day-boundary behavior against the retained local-day proxy.
   This needs a reviewed method, not downloaded observations or relabeling.
3. Define calendar interval applicability in the original jurisdictional timezone,
   then review how it combines with subdaily weather/daylight before rerunning the
   existing static land kernel. Preserve structural-zero and missingness rules.
4. Pilot ordinary, DST, archive-edge and missing-weather days before any regional
   execution. R6 land results remain R6; a native R7 computation/support mapping
   needs separate evidence. No replication of coarse values into R7.

Minimal additional source/method needs: retained weather can support a sampled-statistic
UTC prototype without downloads. Exact hourly/time-integrated weather or precipitation
needs additional appropriate subdaily/accumulation evidence; August 26–31 coverage is
not established by this retained archive. Daylight/calendar need method review. Actual
ferry exposure needs actual departure/arrival or track timing over the requested period
plus reviewed spatial traversal; schedules and a one-day history cannot supply it.
BC monthly reports remain monthly evidence and allocated estimates, not observed UTC
sailings. AIS-dependent water remains blocked on provenance/rights; no new AIS source
is authorized by this pilot.

Resource bound: the actual pilot reads six weather files and a bounded WSF subset;
outputs are small task-local files. A future archive-wide weather pass would read
about 405 MiB and 7.27 million R5 sample rows, before land-kernel propagation. That
regional execution and any new download require a separate scope/resource report.

## Validation

```bash
python -m pytest -q tests/human/activity_and_effort/test_ferry_utc_schedule.py
```

Tests cover UTC date rollover, direction preservation, DST ambiguity, missing time,
null versus zero, partial source-value coverage, invalid totals/identities, duplicate
records and empty input. The regular installed-wheel CI repeats the schedule tests.
