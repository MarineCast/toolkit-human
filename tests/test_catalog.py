from pathlib import Path
from human.core.data.catalog import DATASETS

def test_viewshed_catalog_points_to_current_res7_products():
    root = Path("data/processed/domain/human/viewshed/RES7")
    for source_type in ("land", "water"):
        spec = DATASETS.get(f"human.viewshed.static_{source_type}_r7")
        path = spec.path(
            data_root=Path("data"),
            artifact_root=Path("artifacts"),
            output_root=Path("outputs"),
        )
        assert path == root / f"{source_type.upper()}_STATIC_WEIGHTS_R7.parquet"
        assert spec.primary_key == ("source_h3", "target_h3")
