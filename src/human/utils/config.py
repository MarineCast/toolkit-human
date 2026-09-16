"""Validated, repository-root-relative configuration for human pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Mapping

from human.core.config import ConfigDocument

_COMMON_KEYS = {
    "schema_version",
    "product",
    "category",
    "source",
    "sources",
    "raw",
    "output",
    "inspection",
    "parameters",
    "time",
    "jurisdictions",
    "features",
    "countries",
    "pipeline",
    "app_export",
    "bbox",
    "route_mapping",
    "population",
}


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping.")
    return dict(value)


def reject_unknown(mapping: Mapping[str, Any], allowed: Collection[str], label: str) -> None:
    unknown = sorted(set(mapping).difference(allowed))
    if unknown:
        raise ValueError(f"Unknown keys in {label}: {unknown}")


@dataclass(frozen=True)
class HumanConfig:
    """One fully composed human-family configuration document."""

    document: ConfigDocument
    product: str
    category: str
    raw: dict[str, Any]

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        product: str | None = None,
        category: str | None = None,
        extra_keys: Collection[str] = (),
    ) -> "HumanConfig":
        document = ConfigDocument.load(path)
        raw = require_mapping(document.data, "human configuration")
        reject_unknown(raw, _COMMON_KEYS | set(extra_keys), "human configuration")
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("Human configuration schema_version must equal 1.")
        configured_product = str(raw.get("product", "")).strip()
        configured_category = str(raw.get("category", "")).strip()
        if not configured_product or not configured_category:
            raise ValueError("Human configuration requires product and category.")
        if product is not None and configured_product != product:
            raise ValueError(
                f"Expected human product {product!r}, received {configured_product!r}."
            )
        if category is not None and configured_category != category:
            raise ValueError(
                f"Expected human category {category!r}, received {configured_category!r}."
            )
        for section in ("raw", "output", "inspection"):
            require_mapping(raw.get(section, {}), section)
        return cls(
            document=document,
            product=configured_product,
            category=configured_category,
            raw=raw,
        )

    @property
    def path(self) -> Path:
        return self.document.source

    @property
    def config_hash(self) -> str:
        return self.document.config_hash

    def section(self, name: str) -> dict[str, Any]:
        return require_mapping(self.raw.get(name, {}), name)

    def resolve(self, value: str | Path) -> Path:
        return self.document.resolve_path(value)

    def path_value(self, section: str, key: str, *, default: str | None = None) -> Path:
        values = self.section(section)
        value = values.get(key, default)
        if value in (None, ""):
            raise ValueError(f"Missing required path: {section}.{key}")
        return self.resolve(str(value))

    def manifest_safe(self) -> dict[str, Any]:
        return self.document.redacted_data()
