import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HISTORICAL_SAMPLE_COLUMNS = ["run_id", "ammeter_type", "status", "current_a"]

HISTORICAL_ANALYTICS_COLUMNS = [
    "ammeter_type",
    "source_run_count",
    "valid_sample_count",
    "failed_sample_count",
    "mean_current_a",
    "median_current_a",
    "standard_deviation_a",
    "minimum_current_a",
    "maximum_current_a",
    "current_range_a",
    "coefficient_of_variation",
    "stability_rank",
]

HISTORICAL_FLOAT_COLUMNS = [
    "mean_current_a",
    "median_current_a",
    "standard_deviation_a",
    "minimum_current_a",
    "maximum_current_a",
    "current_range_a",
    "coefficient_of_variation",
]

HISTORICAL_COUNT_COLUMNS = [
    "source_run_count",
    "valid_sample_count",
    "failed_sample_count",
    "stability_rank",
]


@dataclass
class HistoricalAccuracyAssessment:
    ammeter_type: str
    source_run_count: int
    valid_sample_count: int
    failed_sample_count: int
    mean_current_a: Optional[float]
    median_current_a: Optional[float]
    standard_deviation_a: Optional[float]
    minimum_current_a: Optional[float]
    maximum_current_a: Optional[float]
    current_range_a: Optional[float]
    coefficient_of_variation: Optional[float]
    stability_rank: Optional[int]


def write_historical_accuracy_assessment(output_dir: Path) -> None:
    historical_samples = _load_historical_samples(output_dir)
    assessments = _analyze_historical_samples(historical_samples)
    analytics_dir = output_dir / "analytics"

    _write_historical_accuracy_csv(
        analytics_dir / "accuracy_assessment_analytics.csv",
        assessments,
    )
    _write_historical_accuracy_dashboard(
        analytics_dir / "accuracy_assessment_dashboard.png",
        assessments,
        _valid_values_by_ammeter(historical_samples),
    )


def _load_historical_samples(output_dir: Path) -> pd.DataFrame:
    historical_frames: List[pd.DataFrame] = []
    samples_dir = output_dir / "samples"
    if not samples_dir.exists():
        return pd.DataFrame(columns=HISTORICAL_SAMPLE_COLUMNS)

    for run_dir in sorted(path for path in samples_dir.iterdir() if path.is_dir()):
        for ammeter_type, sample_path in _sample_paths_for_run(run_dir).items():
            if not sample_path.exists():
                continue

            try:
                sample_df = pd.read_csv(sample_path)
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue

            if sample_df.empty:
                continue
            if "status" not in sample_df.columns:
                sample_df["status"] = ""
            if "current_a" not in sample_df.columns:
                sample_df["current_a"] = pd.NA

            sample_df = sample_df.assign(run_id=run_dir.name, ammeter_type=ammeter_type)
            historical_frames.append(sample_df.reindex(columns=HISTORICAL_SAMPLE_COLUMNS))

    if not historical_frames:
        return pd.DataFrame(columns=HISTORICAL_SAMPLE_COLUMNS)

    historical_samples = pd.concat(historical_frames, ignore_index=True)
    historical_samples["status"] = (
        historical_samples["status"].fillna("").astype(str).str.strip().str.lower()
    )
    historical_samples["current_a"] = pd.to_numeric(
        historical_samples["current_a"], errors="coerce"
    )
    finite_current = historical_samples["current_a"].map(math.isfinite)
    historical_samples.loc[~finite_current, "current_a"] = pd.NA
    return historical_samples


def _sample_paths_for_run(run_dir: Path) -> Dict[str, Path]:
    sample_paths: Dict[str, Path] = {}
    metadata_path = run_dir / "metadata.json"

    if metadata_path.exists():
        try:
            with metadata_path.open(encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
            samples = metadata.get("artifacts", {}).get("samples", {})
            if isinstance(samples, dict):
                for ammeter_type, sample_artifact in samples.items():
                    sample_filename = sample_artifact
                    if isinstance(sample_artifact, dict):
                        sample_filename = sample_artifact.get("csv")
                    if sample_filename:
                        sample_paths[str(ammeter_type)] = run_dir / str(sample_filename)
        except (OSError, json.JSONDecodeError):
            pass

    for ammeter_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        if ammeter_dir.name == "analytics":
            continue
        sample_path = ammeter_dir / f"{ammeter_dir.name}_samples.csv"
        if sample_path.exists():
            sample_paths.setdefault(ammeter_dir.name, sample_path)

    return sample_paths


def _analyze_historical_samples(
    historical_samples: pd.DataFrame,
) -> List[HistoricalAccuracyAssessment]:
    if historical_samples.empty:
        return []

    assessments: List[HistoricalAccuracyAssessment] = []
    historical_samples = historical_samples.copy()
    historical_samples["is_valid"] = (
        historical_samples["status"].isin(("", "ok"))
        & historical_samples["current_a"].notna()
    )

    for ammeter_type, sample_df in historical_samples.groupby("ammeter_type", sort=False):
        valid_values = sample_df.loc[sample_df["is_valid"], "current_a"]
        failed_sample_count = int((~sample_df["is_valid"]).sum())
        source_run_count = int(sample_df["run_id"].nunique())

        if not valid_values.empty:
            mean_current = float(valid_values.mean())
            standard_deviation = (
                float(valid_values.std(ddof=1)) if len(valid_values) > 1 else 0.0
            )
            minimum_current = float(valid_values.min())
            maximum_current = float(valid_values.max())
            coefficient_of_variation = (
                standard_deviation / abs(mean_current) if mean_current != 0 else None
            )
            assessment = HistoricalAccuracyAssessment(
                ammeter_type=ammeter_type,
                source_run_count=source_run_count,
                valid_sample_count=len(valid_values),
                failed_sample_count=failed_sample_count,
                mean_current_a=mean_current,
                median_current_a=float(valid_values.median()),
                standard_deviation_a=standard_deviation,
                minimum_current_a=minimum_current,
                maximum_current_a=maximum_current,
                current_range_a=maximum_current - minimum_current,
                coefficient_of_variation=coefficient_of_variation,
                stability_rank=None,
            )
        else:
            assessment = HistoricalAccuracyAssessment(
                ammeter_type=ammeter_type,
                source_run_count=source_run_count,
                valid_sample_count=0,
                failed_sample_count=failed_sample_count,
                mean_current_a=None,
                median_current_a=None,
                standard_deviation_a=None,
                minimum_current_a=None,
                maximum_current_a=None,
                current_range_a=None,
                coefficient_of_variation=None,
                stability_rank=None,
            )

        assessments.append(assessment)

    ranked_assessments = sorted(
        [
            assessment
            for assessment in assessments
            if assessment.coefficient_of_variation is not None
        ],
        key=lambda assessment: (
            assessment.coefficient_of_variation,
            assessment.standard_deviation_a,
            assessment.ammeter_type,
        ),
    )

    for rank, assessment in enumerate(ranked_assessments, start=1):
        assessment.stability_rank = rank

    return assessments


def _write_historical_accuracy_csv(
    output_path: Path,
    assessments: List[HistoricalAccuracyAssessment],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_df = pd.DataFrame(
        [
            asdict(assessment)
            for assessment in sorted(assessments, key=_assessment_sort_key)
        ],
        columns=HISTORICAL_ANALYTICS_COLUMNS,
    )
    for column in HISTORICAL_FLOAT_COLUMNS:
        csv_df[column] = pd.to_numeric(csv_df[column], errors="coerce").round(10)
    for column in HISTORICAL_COUNT_COLUMNS:
        csv_df[column] = pd.to_numeric(csv_df[column], errors="coerce").astype("Int64")
    csv_df.to_csv(output_path, index=False)


def _valid_values_by_ammeter(historical_samples: pd.DataFrame) -> Dict[str, List[float]]:
    if historical_samples.empty:
        return {}

    valid_samples = historical_samples[
        historical_samples["status"].isin(("", "ok"))
        & historical_samples["current_a"].notna()
    ]
    return {
        str(ammeter_type): sample_df["current_a"].tolist()
        for ammeter_type, sample_df in valid_samples.groupby("ammeter_type", sort=False)
    }


def _write_historical_accuracy_dashboard(
    output_path: Path,
    assessments: List[HistoricalAccuracyAssessment],
    values_by_ammeter: Dict[str, List[float]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_assessments = sorted(assessments, key=_assessment_sort_key)
    valid_assessments = [
        assessment
        for assessment in sorted_assessments
        if assessment.valid_sample_count > 0
    ]

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    ax_box, ax_cv, ax_table, ax_note = axes.flatten()

    if not valid_assessments:
        for ax in axes.flatten():
            ax.axis("off")
        fig.text(
            0.5,
            0.5,
            "No valid historical samples found",
            ha="center",
            va="center",
            fontsize=14,
        )
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        return

    best_ammeter = valid_assessments[0].ammeter_type
    ammeter_names = [assessment.ammeter_type for assessment in valid_assessments]
    highlight_colors = [
        "#6aa84f" if name == best_ammeter else "#6fa8dc" for name in ammeter_names
    ]
    box_data = [values_by_ammeter[name] for name in ammeter_names]

    box_plot = ax_box.boxplot(box_data, tick_labels=ammeter_names, patch_artist=True)
    for patch, color in zip(box_plot["boxes"], highlight_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    if all(value > 0 for values in box_data for value in values):
        ax_box.set_yscale("log")
        ax_box.set_ylabel("Current (A, log scale)")
    else:
        ax_box.set_ylabel("Current (A)")
    ax_box.set_title("Historical Current Distribution")
    ax_box.grid(True, alpha=0.3)
    ax_box.tick_params(axis="x", rotation=20)

    cv_values = [
        assessment.coefficient_of_variation or 0.0
        for assessment in valid_assessments
    ]
    bars = ax_cv.bar(ammeter_names, cv_values, color=highlight_colors)
    ax_cv.set_title("Relative Stability by Coefficient of Variation")
    ax_cv.set_ylabel("Coefficient of variation")
    ax_cv.grid(True, axis="y", alpha=0.3)
    ax_cv.tick_params(axis="x", rotation=20)
    for bar, assessment in zip(bars, valid_assessments):
        label = f"#{assessment.stability_rank}"
        if assessment.ammeter_type == best_ammeter:
            label = "Most stable"
        ax_cv.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            label,
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax_table.axis("off")
    table_rows = [
        "Mean A",
        "Median A",
        "Std dev A",
        "Min A",
        "Max A",
        "Samples",
    ]
    table_values = [
        [
            _format_metric(assessment.mean_current_a),
            _format_metric(assessment.median_current_a),
            _format_metric(assessment.standard_deviation_a),
            _format_metric(assessment.minimum_current_a),
            _format_metric(assessment.maximum_current_a),
            str(assessment.valid_sample_count),
        ]
        for assessment in valid_assessments
    ]
    table = ax_table.table(
        cellText=list(map(list, zip(*table_values))),
        rowLabels=table_rows,
        colLabels=ammeter_names,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    ax_table.set_title("Summary Statistics")

    ax_note.axis("off")
    note_lines = [
        "Historical Stability Assessment",
        "",
        f"Most stable: {best_ammeter}",
        "Ranking uses coefficient of variation.",
        "Lower values mean more stable repeated measurements.",
        "",
        "This does not measure true accuracy because no shared",
        "reference current is available for these emulator samples.",
    ]
    ax_note.text(
        0.02,
        0.98,
        "\n".join(note_lines),
        ha="left",
        va="top",
        fontsize=11,
        linespacing=1.35,
    )

    fig.suptitle("Historical Ammeter Stability Assessment", fontsize=16)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _assessment_sort_key(assessment: HistoricalAccuracyAssessment) -> Tuple[float, str]:
    rank = (
        assessment.stability_rank
        if assessment.stability_rank is not None
        else math.inf
    )
    return (rank, assessment.ammeter_type)


def _format_metric(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"
