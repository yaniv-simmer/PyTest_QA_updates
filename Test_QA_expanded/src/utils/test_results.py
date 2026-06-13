import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .accuracy_assessment import write_historical_accuracy_assessment
from .config import AmmeterConfig, AppConfig

MEASUREMENT_COLUMNS = [
    "run_id",
    "sample_index",
    "ammeter_type",
    "timestamp_utc",
    "elapsed_seconds",
    "current_a",
    "status",
    "error",
]

SAMPLE_CSV_COLUMNS = [
    "sample_index",
    "ammeter_type",
    "timestamp_utc",
    "elapsed_seconds",
    "current_a",
    "status",
    "error",
]

ANALYTICS_CSV_COLUMNS = [
    "ammeter_type",
    "valid_sample_count",
    "failed_sample_count",
    "mean_current_a",
    "median_current_a",
    "standard_deviation_a",
    "minimum_current_a",
    "maximum_current_a",
    "coefficient_of_variation",
]

ANALYTICS_FLOAT_COLUMNS = [
    "mean_current_a",
    "median_current_a",
    "standard_deviation_a",
    "minimum_current_a",
    "maximum_current_a",
    "coefficient_of_variation",
]

STATUS_OK = "ok"


@dataclass
class TestRunMetadata:
    run_id: str
    started_at_utc: str
    ended_at_utc: str
    status: str
    sampling_config: Dict[str, Any]
    ammeter_config: Dict[str, Any]
    total_samples: int
    valid_samples: int
    failed_samples: int
    artifacts: Dict[str, Any]


def save_test_results(
    config: AppConfig,
    measurements_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    run_id: str,
    started_at_utc: str,
    ended_at_utc: str,
) -> Path:
    output_dir = Path(config.output_dir)
    run_dir = output_dir / "samples" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sample_artifacts = {}
    for ammeter_type in config.ammeters:
        sample_artifacts[ammeter_type] = {
            "csv": f"{ammeter_type}/{ammeter_type}_samples.csv",
            "time_series_plot": f"{ammeter_type}/time_series.png",
        }

    artifacts = {
        "samples": sample_artifacts,
        "metadata": "metadata.json",
        "analytics": {
            "csv": "analytics/analytics.csv",
            "time_series_plot": "analytics/time_series.png",
        },
    }
    measurements_df = _normalize_measurements_dataframe(measurements_df)
    analysis_df = _normalize_analysis_dataframe(analysis_df)

    for ammeter_type, sample_artifact in sample_artifacts.items():
        ammeter_measurements_df = measurements_df[
            measurements_df["ammeter_type"].eq(ammeter_type)
        ]
        _write_samples_csv(run_dir / sample_artifact["csv"], ammeter_measurements_df)
        _write_timeseries_plot(
            run_dir / sample_artifact["time_series_plot"],
            {ammeter_type: config.ammeters[ammeter_type]},
            ammeter_measurements_df,
            title=f"{ammeter_type} Current Measurements Over Time",
        )

    _write_analytics_csv(
        run_dir / artifacts["analytics"]["csv"],
        analysis_df,
    )
    _write_timeseries_plot(
        run_dir / artifacts["analytics"]["time_series_plot"],
        config.ammeters,
        measurements_df,
    )
    _write_metadata_json(
        run_dir / artifacts["metadata"],
        config,
        measurements_df,
        artifacts,
        run_id,
        started_at_utc,
        ended_at_utc,
    )
    write_historical_accuracy_assessment(output_dir)

    return run_dir


def _normalize_measurements_dataframe(measurements_df: pd.DataFrame) -> pd.DataFrame:
    measurements_df = measurements_df.reindex(columns=MEASUREMENT_COLUMNS).copy()
    for column in ("elapsed_seconds", "current_a"):
        measurements_df[column] = pd.to_numeric(measurements_df[column], errors="coerce")
    return measurements_df


def _normalize_analysis_dataframe(analysis_df: pd.DataFrame) -> pd.DataFrame:
    analysis_df = analysis_df.reindex(columns=ANALYTICS_CSV_COLUMNS).copy()
    for column in ANALYTICS_FLOAT_COLUMNS:
        analysis_df[column] = pd.to_numeric(analysis_df[column], errors="coerce")
    return analysis_df


def _write_samples_csv(output_path: Path, measurements_df: pd.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_df = measurements_df.reindex(columns=SAMPLE_CSV_COLUMNS).copy()
    for column in ("elapsed_seconds", "current_a"):
        csv_df[column] = pd.to_numeric(csv_df[column], errors="coerce").round(10)
    csv_df.to_csv(output_path, index=False)


def _write_analytics_csv(output_path: Path, analytics_df: pd.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_df = analytics_df.reindex(columns=ANALYTICS_CSV_COLUMNS).copy()
    for column in ANALYTICS_FLOAT_COLUMNS:
        csv_df[column] = pd.to_numeric(csv_df[column], errors="coerce").round(10)
    csv_df.to_csv(output_path, index=False)


def _write_metadata_json(
    output_path: Path,
    config: AppConfig,
    measurements_df: pd.DataFrame,
    artifacts: Dict[str, Any],
    run_id: str,
    started_at_utc: str,
    ended_at_utc: str,
) -> None:
    total_samples = len(measurements_df)
    failed_samples = int(measurements_df["status"].ne(STATUS_OK).sum())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = TestRunMetadata(
        run_id=run_id,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        status="completed" if failed_samples == 0 else "completed_with_errors",
        sampling_config=asdict(config.sampling),
        ammeter_config={
            name: {
                "class": ammeter.class_name,
                "module": ammeter.module,
                "port": ammeter.port,
                "command": ammeter.command,
            }
            for name, ammeter in config.ammeters.items()
        },
        total_samples=total_samples,
        valid_samples=total_samples - failed_samples,
        failed_samples=failed_samples,
        artifacts=artifacts,
    )

    with output_path.open("w", encoding="utf-8") as json_file:
        json.dump(asdict(metadata), json_file, indent=2)


def _write_timeseries_plot(
    output_path: Path,
    ammeters: Dict[str, AmmeterConfig],
    measurements_df: pd.DataFrame,
    title: str = "Current Measurements Over Time",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    plotted = False

    for ammeter_type in ammeters:
        valid_samples_df = measurements_df[
            measurements_df["ammeter_type"].eq(ammeter_type)
            & measurements_df["status"].eq(STATUS_OK)
            & measurements_df["current_a"].notna()
        ]
        if not valid_samples_df.empty:
            plotted = True
            elapsed_seconds = valid_samples_df["elapsed_seconds"]
            current_values = valid_samples_df["current_a"]
            (sample_line,) = ax.plot(
                elapsed_seconds,
                current_values,
                marker="o",
                linewidth=1.5,
                label=ammeter_type,
            )
            line_color = sample_line.get_color()
            mean_current = current_values.mean()
            median_current = current_values.median()
            ax.axhline(
                mean_current,
                color=line_color,
                linestyle="--",
                linewidth=1.2,
                alpha=0.85,
                label=f"{ammeter_type} mean ({mean_current:.3f} A)",
            )
            ax.axhline(
                median_current,
                color=line_color,
                linestyle=":",
                linewidth=1.4,
                alpha=0.95,
                label=f"{ammeter_type} median ({median_current:.3f} A)",
            )

    if not plotted:
        ax.text(0.5, 0.5, "No valid samples collected", ha="center", va="center")

    ax.set_title(title)
    ax.set_xlabel("Elapsed time (seconds)")
    ax.set_ylabel("Current (A)")
    ax.grid(True, alpha=0.3)
    if plotted:
        ax.legend(fontsize="small")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
