# Architecture

The supported API is `human.get_human_data`; its CLI is `human get-human-data`. Supported source is grouped by pipeline:

```text
human/pipelines/
  delivery/    run.py, settings.py, finalize.py, validation.py
  population/  delivery.py, collection.py, collected.py, water_support.py, gaf.py
    static/    api.py, census.py, sources.py, geometry.py, engine.py
  native/      collection.py, features.py, summary.py
  calendar/    lookup.py
human/utils/   artifact/checksum publication, bounded downloads, shared study reader
```

Delivery composes collection, scientific producers, exact schema validation, staged publication/rollback and run-owned cleanup. Population retains the original full-polygon allocation engine and explicit source/support qualifiers. Native collection retains original snapshots and receipts outside output. Calendar retains jurisdiction/local civil-date support; it is not an H3 UTC-day product. New code imports canonical pipeline paths. Earlier root/domain paths are thin module compatibility imports, preserving direct callers and module-level patch behavior without maintaining duplicate implementations. Historical domain/research modules remain outside the supported output surface.

`human/utils` contains genuinely shared artifact/checksum, download and study helpers; population algorithms and native classification remain in their pipelines. The retained legacy study exporter and supported population delivery use the same Parquet writer. The independent Canada Profile reader remains a domain helper shared with its retained research producer. No application import or app export is used.

The generated catalog describes only the three final products. Workspace initialization installs the supported recipe, five-source acquisition specification and safe environment example. Runtime settings are pure preflight with explicit arguments > process environment > .env; dates affect calendar only. No Meteorology append/rebuild behavior is borrowed. See [the pipeline guide](get-human-data.md).

Other family/research modules are compatibility code, not supported products or CLI routes. AIS remains absent. Viewshed, ferry, routing and model proxies are excluded from this delivery. Common infrastructure under `human.core` remains local; independent installations provide the approved study/registry/physical-context artifacts and census source inputs. The MarineCast paths are a deployment recipe, never sibling Python imports.
