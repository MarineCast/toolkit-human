# AIS data sources

The current adapter snapshots provenance-declared yearly Parquet inputs. The
available legacy archive contains MMSI/H3/hour aggregates for 2020–2024 at H3
resolution 6, but its original acquisition endpoint, license, and completeness
are unresolved. Strict builds therefore fail unless a complete source is
configured; `--allow-partial` is explicitly research-only. Track distance,
exact duration, and acoustic exposure require a future raw-position source.
