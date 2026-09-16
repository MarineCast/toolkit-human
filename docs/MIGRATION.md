# Migration from OrcaCast

The extraction includes the human domain, its shared utility dependencies, configuration,
family and geometry tests, catalog generator, ferry/AIS acquisition notebooks, legacy
viewshed notebooks and domain-specific documentation. Current source files were copied,
including uncommitted changes; the source was not reset to HEAD.

[migration_inventory.json](migration_inventory.json) records original relative paths,
destination paths, pre-transform SHA-256 hashes and move versus shared-copy status.
Namespace imports changed from the former application domain to `human`; infrastructure
imports use `human.core`. Workspace resolution replaces assumptions about source-tree depth.
Original artifact locations and metadata schemas are retained unless explicitly documented.

Application-specific experiments and publishing tests remain in OrcaCast's evaluation and
publishing directories. All human producers, producer CLI registrations, configurations,
catalog registrations and domain tests are removed from OrcaCast; no forwarding package
remains. Application consumers use the external package. Original Git history and generated
research data are preserved; this is a source migration, not a data deletion or history rewrite.

The standalone viewshed toolkit already exists. Migrating the remaining legacy observation
geometry here preserves its consumers and local modifications; replacement with the
standalone implementation needs separate scientific equivalence work. AIS remains with
these human products until a separately scoped toolkit-ais migration.

Validation results and limitations are recorded in [VALIDATION.md](VALIDATION.md). No remote push, package
publication, data download, regional rebuild or production promotion is part of this extraction.
