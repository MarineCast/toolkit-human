"""Summarize a completed experiment and attach observer-source strata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from human.utils.artifacts import (
    atomic_write_json,
    atomic_write_parquet,
    sha256_file,
)

from .experiment import allocation_scores
from .strata import target_context


def markdown_table(frame: pd.DataFrame) -> str:
    def cell(value):
        text = f"{value:.4f}" if isinstance(value, float) else str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    rows = [
        "| " + " | ".join(map(str, frame.columns)) + " |",
        "| " + " | ".join("---" for _ in frame.columns) + " |",
    ]
    rows.extend(
        "| " + " | ".join(cell(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    )
    return "\n".join(rows)


def write_report(run: Path, root: Path) -> Path:
    metadata = json.loads((run / "manifest.json").read_text())
    context = target_context(root)
    atomic_write_parquet(context, run / "observer_source_context.parquet", overwrite=True)
    rows = []
    access_features = {}
    for path in (
        sorted(run.glob("*_baseline.parquet"))
        + sorted(run.glob("*_coverage.parquet"))
        + sorted(run.glob("*_composite.parquet"))
        + sorted(run.glob("*_components.parquet"))
        + sorted(run.glob("*_combined.parquet"))
    ):
        name = path.stem
        ecotype, tail = name.split("_", 1)
        if ecotype not in {"SRKW", "TRANSIENT"}:
            continue
        variant = (
            "confirmed_plus_imputed"
            if tail.startswith("confirmed_plus_imputed")
            else "confirmed_only"
        )
        evaluation = "spatial_blocked" if "spatial_blocked" in tail else "temporal"
        fold, formulation = tail.rsplit("_", 2)[-2:]
        frame = pd.read_parquet(path).merge(context, on="h3", how="left", validate="many_to_one")
        if ecotype not in access_features:
            access_features[ecotype] = pd.read_parquet(
                run / f"{ecotype.lower()}_cell_features.parquet",
                columns=[
                    "week_start",
                    "h3",
                    "previous_week_land_mapped_access_support",
                    "previous_week_land_verified_access_support",
                ],
            )
        frame = frame.merge(
            access_features[ecotype], on=["week_start", "h3"], how="left", validate="one_to_one"
        )
        frame["access_evidence_tier"] = "outside_or_unknown"
        frame.loc[frame.previous_week_land_mapped_access_support.eq(0), "access_evidence_tier"] = (
            "no_mapped_evidence"
        )
        frame.loc[frame.previous_week_land_mapped_access_support.gt(0), "access_evidence_tier"] = (
            "mapped_not_verified"
        )
        frame.loc[
            frame.previous_week_land_verified_access_support.gt(0), "access_evidence_tier"
        ] = "verified_public_supported"
        for field in ("source_jurisdiction", "source_landmass", "access_evidence_tier"):
            for label, subset in frame.groupby(frame[field].fillna("outside_or_unknown")):
                identity = {
                    "ecotype": ecotype,
                    "variant": variant,
                    "evaluation": evaluation,
                    "fold": int(fold),
                    "formulation": formulation,
                    "stratum": f"{field}:{label}",
                }
                rows.extend(identity | row for row in allocation_scores(subset))
    atomic_write_parquet(
        pd.DataFrame(rows), run / "observer_stratified_scores.parquet", overwrite=True
    )
    strata = pd.concat(
        [pd.DataFrame(rows), pd.read_parquet(run / "stratified_scores.parquet")], ignore_index=True
    )
    strata_summary = (
        strata.groupby(["ecotype", "variant", "evaluation", "stratum", "formulation"])
        .agg(
            ndcg_at_10=("ndcg_at_10", "mean"),
            cross_entropy=("cross_entropy", "mean"),
            recall_at_10=("recall_at_10", "mean"),
            positive_weeks=("week_start", "nunique"),
        )
        .reset_index()
    )
    atomic_write_parquet(strata_summary, run / "strata_summary.parquet", overwrite=True)
    strata_primary = strata_summary.loc[strata_summary.variant.eq("confirmed_only")]
    strata_deltas = strata_primary.pivot(
        index=["ecotype", "evaluation", "stratum"], columns="formulation", values="ndcg_at_10"
    )
    strata_deltas = strata_deltas.dropna(subset=["baseline", "combined"])
    strata_deltas["combined_minus_baseline"] = strata_deltas.combined - strata_deltas.baseline
    summary = pd.read_parquet(run / "summary.parquet")
    primary = summary.loc[summary.variant.eq("confirmed_only")]
    intervals = pd.DataFrame(json.loads((run / "paired_bootstrap.json").read_text()))
    intervals = intervals.loc[
        intervals.variant.eq("confirmed_only")
        & intervals.block_weeks.eq(8)
        & intervals.metric.eq("ndcg_at_10")
    ]
    text = "# Land-effort modeling results\n\n"
    text += "Status: evaluated research inputs; incumbent unchanged; not production-promoted.\n\n"
    text += (
        "## Confirmed-sighting allocation\n\n"
        + markdown_table(primary.drop(columns="variant"))
        + "\n\n"
    )
    text += "## Paired NDCG differences versus baseline\n\nPositive values favor the added feature formulation. Eight-week block bootstrap, 95% intervals; four- and thirteen-week results are retained in JSON.\n\n"
    text += (
        markdown_table(
            intervals[
                [
                    "ecotype",
                    "evaluation",
                    "formulation",
                    "delta",
                    "lower_95",
                    "upper_95",
                    "paired_weeks",
                ]
            ]
        )
        + "\n\n"
    )
    text += "## Where the combined formulation helps or fails\n\n"
    text += "Confirmed sightings; mean NDCG@10 within each stratum. Candidate sets are restricted to the stratum for this diagnostic, so these are not directly comparable to pooled scores. Small strata can be unstable; this table is descriptive, not evidence of promotion. All five formulations, positive-week counts, cross-entropy and recall are saved in `strata_summary.parquet`.\n\n"
    text += (
        markdown_table(
            strata_deltas[["baseline", "combined", "combined_minus_baseline"]].reset_index()
        )
        + "\n\n"
    )
    text += "## Interpretation and limits\n\n"
    text += "- These are allocations of recorded reports on a fixed canonical water universe, not whale-absence predictions or measured observer-hours.\n"
    text += "- The baseline is the same packaged season/location/report-history estimator without land covariates, not a previously frozen production model.\n"
    text += "- All five formulations use the same folds, candidate cells, uniform training sample and preprocessing recipe. Scaling and imputation are fitted within each training fold.\n"
    text += "- Spatial holdouts remove held-out reports from both history features and training-week eligibility. Source-snapshot context remains retrospective.\n"
    text += f"- Regional Brier/calibration availability: **{metadata['coverage']['reason']}**. Retrieval watermarks are not proof of complete observation.\n"
    text += "- Jurisdiction/island strata refer to the dominant physical observer-source support (75% threshold), not maritime sovereignty or the location of a reported whale. Missing/mixed labels remain explicit. Access tiers indicate any positively weighted verified support, otherwise mapped-only support, otherwise no mapped evidence or unknown; they are not a declaration that every shore is public. Dynamic coverage strata are also saved.\n"
    text += "- The combined model is not automatically promoted when one subgroup or metric improves. Paired evidence and operational freshness must be reviewed before any production change.\n"
    text += "- The confirmed-plus-imputed sensitivity can equal confirmed-only when no additional certified records qualify.\n\n"
    text += f"Routing policy: `{metadata['routing_policy']}`.\n"
    output = run / "REPORT.md"
    output.write_text(text)
    atomic_write_json(
        run / "report_artifacts.json",
        {
            p.name: sha256_file(p)
            for p in (
                output,
                run / "observer_source_context.parquet",
                run / "observer_stratified_scores.parquet",
                run / "strata_summary.parquet",
            )
        },
        overwrite=True,
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(write_report(args.run, args.root))


if __name__ == "__main__":
    main()
