import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .accuracy_assessment import write_historical_accuracy_assessment


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
    config: Dict[str, Any],
    measurements: List[Any],
    analysis: Dict[str, Any],
    run_id: str,
    started_at_utc: str,
    ended_at_utc: str,
) -> Path:
    output_dir = Path(config.get("result_management", {}).get("output_dir", "results"))
    run_dir = output_dir / "samples" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sample_artifacts = {}
    for ammeter_type in config.get("ammeters", {}):
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

    for ammeter_type, sample_artifact in sample_artifacts.items():
        ammeter_measurements = [
            sample for sample in measurements if sample.ammeter_type == ammeter_type
        ]
        _write_samples_csv(run_dir / sample_artifact["csv"], ammeter_measurements)
        _write_timeseries_plot(
            run_dir / sample_artifact["time_series_plot"],
            {ammeter_type: config.get("ammeters", {})[ammeter_type]},
            ammeter_measurements,
            title=f"{ammeter_type} Current Measurements Over Time",
        )

    _write_analytics_csv(
        run_dir / artifacts["analytics"]["csv"],
        config.get("ammeters", {}),
        analysis,
    )
    _write_timeseries_plot(
        run_dir / artifacts["analytics"]["time_series_plot"],
        config.get("ammeters", {}),
        measurements,
    )
    _write_metadata_json(
        run_dir / artifacts["metadata"],
        config,
        measurements,
        artifacts,
        run_id,
        started_at_utc,
        ended_at_utc,
    )
    write_historical_accuracy_assessment(output_dir)

    return run_dir


def _write_samples_csv(output_path: Path, measurements: List[Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_index",
        "ammeter_type",
        "timestamp_utc",
        "elapsed_seconds",
        "current_a",
        "status",
        "error",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for sample in measurements:
            row = asdict(sample)
            row.pop("run_id")
            row["elapsed_seconds"] = _csv_value(sample.elapsed_seconds)
            row["current_a"] = _csv_value(sample.current_a)
            writer.writerow(row)


def _write_analytics_csv(
    output_path: Path,
    ammeters: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
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

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for ammeter_type in ammeters:
            row = asdict(analysis[ammeter_type])
            row.pop("run_id")
            for fieldname in fieldnames:
                if isinstance(row[fieldname], float):
                    row[fieldname] = _csv_value(row[fieldname])
            writer.writerow(row)


def _write_metadata_json(
    output_path: Path,
    config: Dict[str, Any],
    measurements: List[Any],
    artifacts: Dict[str, Any],
    run_id: str,
    started_at_utc: str,
    ended_at_utc: str,
) -> None:
    total_samples = len(measurements)
    failed_samples = sum(1 for sample in measurements if sample.status != "ok")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = TestRunMetadata(
        run_id=run_id,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        status="completed" if failed_samples == 0 else "completed_with_errors",
        sampling_config=config.get("testing", {}).get("sampling", {}),
        ammeter_config=config.get("ammeters", {}),
        total_samples=total_samples,
        valid_samples=total_samples - failed_samples,
        failed_samples=failed_samples,
        artifacts=artifacts,
    )

    with output_path.open("w", encoding="utf-8") as json_file:
        json.dump(asdict(metadata), json_file, indent=2)


def _write_timeseries_plot(
    output_path: Path,
    ammeters: Dict[str, Any],
    measurements: List[Any],
    title: str = "Current Measurements Over Time",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    plotted = False

    for ammeter_type in ammeters:
        valid_samples = [
            sample
            for sample in measurements
            if sample.ammeter_type == ammeter_type
            and sample.status == "ok"
            and sample.current_a is not None
        ]
        if valid_samples:
            plotted = True
            ax.plot(
                [sample.elapsed_seconds for sample in valid_samples],
                [sample.current_a for sample in valid_samples],
                marker="o",
                linewidth=1.5,
                label=ammeter_type,
            )

    if not plotted:
        ax.text(0.5, 0.5, "No valid samples collected", ha="center", va="center")

    ax.set_title(title)
    ax.set_xlabel("Elapsed time (seconds)")
    ax.set_ylabel("Current (A)")
    ax.grid(True, alpha=0.3)
    if plotted:
        ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _csv_value(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 10)