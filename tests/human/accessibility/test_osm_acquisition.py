from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from human.utils import osm, wfs


def _frame(osm_id: str = "1") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"OSM_TYPE": ["node"], "OSM_ID": [osm_id]},
        geometry=[Point(-122.5, 48.5)],
        crs="EPSG:4326",
    )


def test_overpass_endpoint_failover(monkeypatch) -> None:
    calls: list[str] = []

    def fake_query(endpoint: str, _query: str, *, timeout_seconds: int):
        del timeout_seconds
        calls.append(endpoint)
        if endpoint == "https://primary.invalid":
            raise RuntimeError("504 gateway timeout")
        return _frame()

    monkeypatch.setattr(osm, "query_overpass_points", fake_query)
    result = osm.query_overpass_points_first_available(
        ("https://primary.invalid", "https://fallback.invalid"),
        "fixture query",
    )
    assert len(result) == 1
    assert calls == ["https://primary.invalid", "https://fallback.invalid"]


def test_overpass_tiles_are_all_required_and_deduplicated(monkeypatch) -> None:
    calls = []

    def fake_query(_endpoints, query: str, *, timeout_seconds: int):
        del timeout_seconds
        calls.append(query)
        return _frame("shared")

    monkeypatch.setattr(osm, "query_overpass_points_first_available", fake_query)
    result = osm.query_overpass_points_batched(
        ("https://fixture.invalid",),
        (0.0, 0.0, 3.0, 3.0),
        ('["leisure"="slipway"]',),
        maximum_span_degrees=2.0,
    )
    assert len(calls) == 4
    assert len(result) == 1


def test_invalid_overpass_tile_span_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        osm.tiled_bboxes((0.0, 0.0, 1.0, 1.0), maximum_span_degrees=0)


def test_wfs_acquisition_pages_until_number_matched(monkeypatch) -> None:
    calls: list[int] = []

    class Response:
        def __init__(self, start_index: int):
            self.start_index = start_index

        def raise_for_status(self) -> None:
            return None

        def json(self):
            features = []
            if self.start_index < 2:
                features = [
                    {
                        "type": "Feature",
                        "properties": {"OBJECTID": self.start_index + 1},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [-123.0, 49.0],
                        },
                    }
                ]
            return {
                "type": "FeatureCollection",
                "numberMatched": 2,
                "features": features,
            }

    def fake_get(_url, *, params, headers, timeout):
        del headers, timeout
        calls.append(params["startIndex"])
        return Response(params["startIndex"])

    monkeypatch.setattr(wfs.requests, "get", fake_get)
    result = wfs.query_wfs_geojson(
        "https://fixture.invalid/wfs",
        "pub:fixture",
        (-124.0, 48.0, -122.0, 50.0),
        page_size=1,
    )
    assert calls == [0, 1]
    assert result["OBJECTID"].tolist() == [1, 2]
