# Water observation-opportunity data sources

This research product consumes only locally materialized, checksum-bound inputs:

- H3 R6 AIS daily class activity and its manifest. The values are unique-vessel-hour proxies, not true durations.
- H3 R7 ferry rider/platform activity and its manifest. Ferry is propagated at R7 and is not deduplicated against passenger AIS.
- H3 R5 surface visibility, wind, and precipitation plus deterministic H3 R4 daylight.
- The validated compact H3 R7 water static viewshed and active viewshed configuration.
- The immutable canonical positive-sightings release for validation only; sightings never construct or scale a proxy.
- Land source/access tables for WA/BC discontinuity diagnostics only.

Whale-watch tracks, direct observer effort, reporting capture, and reconstructed sea state are unavailable. They remain null with `source_unavailable`; they are never assigned neutral values or zeros.

