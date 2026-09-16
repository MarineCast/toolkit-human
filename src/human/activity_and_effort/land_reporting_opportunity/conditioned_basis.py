"""Install represented-site kernels in the existing daily sparse basis."""

from __future__ import annotations

from dataclasses import replace

import h3
import numpy as np
import pandas as pd
from scipy import sparse

from .components import STREAM_COMPONENTS


def condition_basis(cfg, general, source, kernel, support):
    """Retain general reachability streams; condition the primary on access sites."""
    from .access_kernel import ACCESS_KERNEL_VERSION, kernel_digest, support_digest
    from .support import WATER_SUPPORT_VERSION

    if not kernel.empty and set(kernel.KERNEL_ALGORITHM_VERSION) != {ACCESS_KERNEL_VERSION}:
        raise ValueError("Incompatible access kernel algorithm; rebuild prerequisites")
    if set(support.WATER_SUPPORT_VERSION) != {WATER_SUPPORT_VERSION}:
        raise ValueError("Incompatible canonical water support; rebuild prerequisites")
    if kernel.duplicated(["source_h3", "target_h3", "SCENARIO", "DISTANCE_BIN_INDEX"]).any():
        raise ValueError("Access kernel contains duplicate support rows")
    plan = support.attrs.get("evaluation_plan", {})
    if (
        plan.get("state") != "complete"
        or plan.get("kernel_digest") != kernel_digest(kernel)
        or plan.get("support_digest") != support_digest(support)
    ):
        raise ValueError(
            "Incomplete or uncertified access kernel: geometry remains unresolved; rebuild all planned samples"
        )
    if (
        not np.isfinite(kernel[["canopy_kernel", "bare_earth_kernel", "evaluated_support"]])
        .all()
        .all()
    ):
        raise ValueError("Nonfinite kernel computation remains unresolved")
    if not set(kernel.target_h3).issubset(set(support.target_h3)):
        raise ValueError("Kernel target lies outside certified water support")
    planned = pd.DataFrame(plan["samples"])
    scenario_counts = {
        scenario: planned.loc[
            (planned.SAMPLE_WEIGHT * planned[f"{scenario}_SITE_WEIGHT"]) > 0, "source_h3"
        ].nunique()
        for scenario in ("MAPPED", "VERIFIED")
    }
    if not set(planned.source_h3).issubset(set(source.source_h3)):
        raise ValueError("Kernel source missing from activity domain")
    keys = list(general.target_order)
    target_codes = {key: i for i, key in enumerate(keys)}
    n = len(keys)
    frame = kernel.merge(
        support[["target_h3", "H3_INDEX", "R6_WATER_AREA_WEIGHT"]],
        on="target_h3",
        validate="many_to_one",
    ).merge(source, on="source_h3", validate="many_to_one")
    frame["WEATHER_H3_R5"] = frame.source_h3.map(lambda cell: h3.cell_to_parent(cell, 5))
    bins = max(
        len(general.distance_km), int(frame.DISTANCE_BIN_INDEX.max()) + 1 if len(frame) else 1
    )
    old = {item["weather_h3_r5"]: item for item in general.weather_bases}
    regions = sorted(set(old) | {h3.cell_to_parent(c, 5) for c in planned.source_h3})
    bases = []
    static = general.target_static.copy()
    static["GENERAL_LAND_STATIC_VIEWABILITY_RAW"] = static["PHYSICAL_VIEWABILITY_STATIC_RAW"]
    totals_all = np.zeros((len(STREAM_COMPONENTS), n))
    values_all = np.zeros_like(totals_all)
    required_all = np.zeros_like(totals_all)
    for region in regions:
        previous = old.get(region)
        blocks = []
        totals = []
        diagnostic = sparse.csr_matrix((n, bins))
        for index, (stream, _) in enumerate(STREAM_COMPONENTS):
            if stream not in {
                "PHYSICAL_VIEWABILITY",
                "LAND_EFFORT_PROXY",
                "VERIFIED_LAND_EFFORT_PROXY",
            }:
                block = (
                    previous["matrix_transpose"][index * n : (index + 1) * n]
                    if previous
                    else sparse.csr_matrix((n, len(general.distance_km)))
                )
                block = sparse.hstack(
                    [block, sparse.csr_matrix((n, bins - block.shape[1]))], format="csr"
                )
                total = (
                    previous["context_totals"][index * n : (index + 1) * n]
                    if previous
                    else np.zeros(n)
                )
            else:
                scenario = "VERIFIED" if stream == "VERIFIED_LAND_EFFORT_PROXY" else "MAPPED"
                part = frame.loc[
                    (frame.WEATHER_H3_R5 == region) & (frame.SCENARIO == scenario)
                ].copy()
                activity = (
                    np.ones(len(part))
                    if stream == "PHYSICAL_VIEWABILITY"
                    else part.LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX.to_numpy()
                )
                weights = part.R6_WATER_AREA_WEIGHT.to_numpy()
                available = np.isfinite(activity)
                rr = part.H3_INDEX.map(target_codes).to_numpy(dtype=int)
                cc = part.DISTANCE_BIN_INDEX.to_numpy(dtype=int)
                values = weights * part.canopy_kernel.to_numpy() * activity
                block = sparse.csr_matrix((np.nan_to_num(values), (rr, cc)), shape=(n, bins))
                # Weather is required only for contributions that can affect this
                # stream. Unknown activity stays required; proven activity zero
                # and zero physical kernels are independent of weather.
                relevant = (part.canopy_kernel.to_numpy() > 0) & (~available | (activity > 0))
                required = weights * part.evaluated_support.to_numpy() * relevant
                required_all[index] += np.bincount(rr, weights=required, minlength=n)
                context = required * available
                total = np.bincount(rr, weights=context, minlength=n)
                if stream == "PHYSICAL_VIEWABILITY":
                    diagnostic = sparse.csr_matrix((context, (rr, cc)), shape=(n, bins))
            blocks.append(block)
            totals.append(total)
            totals_all[index] += total
            values_all[index] += np.asarray(block.sum(axis=1)).ravel()
        bases.append(
            {
                "weather_h3_r5": region,
                "daylight_h3_r4": h3.cell_to_parent(region, 4),
                "matrix_transpose": sparse.vstack(blocks, format="csr"),
                "diagnostic_matrix_transpose": diagnostic,
                "context_totals": np.concatenate(totals),
            }
        )
    physical_total = totals_all[0]
    expected_physical = float(scenario_counts.get("MAPPED", 0))
    for index, (stream, _) in enumerate(STREAM_COMPONENTS):
        if stream not in {
            "PHYSICAL_VIEWABILITY",
            "LAND_EFFORT_PROXY",
            "VERIFIED_LAND_EFFORT_PROXY",
        }:
            continue
        values = values_all[index].copy()
        values[(totals_all[index] == 0) & (required_all[index] > 0)] = np.nan
        static[f"{stream}_STATIC_RAW"] = values
        scenario = "VERIFIED" if stream == "VERIFIED_LAND_EFFORT_PROXY" else "MAPPED"
        expected = float(scenario_counts.get(scenario, 0))
        if expected == 0:
            values[:] = np.nan
            static[f"{stream}_STATIC_RAW"] = values
        static[f"{stream}_CONTEXT_STATIC_SUPPORT"] = required_all[index]
        static[f"{stream}_PROVEN_ZERO"] = (required_all[index] == 0) & (expected > 0)
        # Static access describes the represented inventory, independently of
        # successful geometry, activity inputs, or the weather on a given day.
        static[f"{stream}_REGIONAL_INVENTORY_SOURCE_FRACTION"] = (
            expected / expected_physical if expected_physical > 0 else np.nan
        )
        # Preserve old meaning only as an explicitly deprecated compatibility alias.
        static[f"{stream}_STATIC_CONTEXT_FRACTION"] = static[
            f"{stream}_REGIONAL_INVENTORY_SOURCE_FRACTION"
        ]
    mapped = frame.loc[frame.SCENARIO == "MAPPED"].copy()
    verified = frame.loc[frame.SCENARIO == "VERIFIED_REFERENCE"].copy()
    local_values = []
    for part in (mapped, verified):
        value = (
            part.R6_WATER_AREA_WEIGHT
            * part.canopy_kernel
            * part.LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX
        )
        local_values.append(
            value.groupby(part.H3_INDEX).sum(min_count=1).reindex(keys, fill_value=0).to_numpy()
        )
    denominator, numerator = local_values
    unresolved = mapped.loc[
        (mapped.canopy_kernel > 0) & mapped.LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX.isna(),
        "H3_INDEX",
    ]
    valid = (denominator > 0) & ~pd.Index(keys).isin(unresolved)
    if (numerator[valid] > denominator[valid] + 1e-10).any():
        raise ValueError("Verified reference support is not a subset of mapped reference support")
    static["TARGET_LOCAL_MAPPED_REFERENCE_STATIC_RAW"] = denominator
    static["TARGET_LOCAL_VERIFIED_REFERENCE_STATIC_RAW"] = numerator
    static["TARGET_LOCAL_VERIFIED_REFERENCE_FRACTION"] = np.divide(
        numerator, denominator, out=np.full(n, np.nan), where=valid
    )
    static["TARGET_LOCAL_VERIFIED_REFERENCE_STATE"] = np.select(
        [pd.Index(keys).isin(unresolved), denominator <= 0, numerator > 0],
        ["unknown", "not_applicable", "positive"],
        default="derived_zero",
    )
    static["MODELED_WATER_AREA_M2"] = static.H3_INDEX.map(
        support.groupby("H3_INDEX").target_water_area_m2.sum()
    )
    static["ACCESS_MAPPING_COMPLETENESS"] = np.nan
    static["EVALUATED_GEOMETRIC_SUPPORT_FRACTION"] = 1.0 if expected_physical > 0 else np.nan
    return replace(
        general,
        target_static=static,
        weather_bases=bases,
        distance_km=np.arange(bins) * cfg.dynamic_distance_bin_km,
    )
