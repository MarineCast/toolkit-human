# Static viewshed maps

The canonical viewshed configuration is
`config/modeling/effort/viewshed.yaml`. The production map command reads the
finalized static-weight artifacts from
`data/processed/domain/human/viewshed/RES7/` and writes interactive HTML pages:

```bash
python -m human.viewshed.cli.main \
  export-static-maps \
  --config config/modeling/effort/viewshed.yaml
```

Use `--overwrite` after changing the config or when intentionally replacing an
existing map export.

The complete legacy workflow is also available through `human.viewshed.run_viewshed`;
review its stage and cleanup settings before execution.

## Configured location

Set the point under `static_maps.selected_location`:

```yaml
static_maps:
  enabled: true
  selected_location:
    latitude: 48.135238
    longitude: -122.767230
```

The command converts this point to the configured source H3 resolution. That
source cell must exist in `LAND_STATIC_WEIGHTS_R{resolution}.parquet`; otherwise
the command fails with the missing H3 cell and coordinates.

## Outputs

The command writes these HTML pages under the resolution-aware
`outputs/effort/viewshed/h3r{source_resolution}/static/` directory rooted at
`paths.map_dir`:

- `land_source_selected_location_static_weights.html`: distance, vegetation, terrain, and
  combined pair weights for the configured source cell. Every factor has an
  original H3 layer and a smoothed layer. The exact configured point, selected
  source H3 cell, and viewshed radius are context layers.
- `land_source_aggregate_static_weights.html`: source-grid and target-grid sums
  from finalized land-source pairs.
- `water_source_aggregate_static_weights.html`: source-grid and target-grid sums
  from finalized water-source pairs. Every source/target factor has original H3
  and smoothed layers.

The export also writes the selected, source-aggregate, and target-aggregate
Parquet value tables, a manifest, and shared GeoJSON/PNG/GeoTIFF assets used by
the HTML pages. Native H3 layers expose the unscaled grid value on hover; map
clicks provide copyable latitude/longitude.

## Static factor semantics

- `distance` is the persisted H3-centroid logistic diagnostic.
- `terrain` is the bare-earth observer-to-water-pixel LOS kernel. It already
  integrates the configured logistic distance curve at the pixel-pair level.
- `vegetation` is conditional canopy retention and is displayed only where
  bare-earth terrain support is positive.
- `combined` is terrain multiplied by conditional vegetation. The centroid
  distance diagnostic is not multiplied a second time.

Target maps sum each factor across sources. Source maps sum each factor across
targets. Weather and daylight weights are intentionally excluded.
