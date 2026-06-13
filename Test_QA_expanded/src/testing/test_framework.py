import csv
import importlib
import json
import math
import statistics
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.client import request_current_from_ammeter
from ..utils.config import load_config


@dataclass
class MeasurementSample:
    run_id: str
    sample_index: int
    ammeter_type: str
    timestamp_utc: str
    elapsed_seconds: float
    current_a: Optional[float]
    status: str
    error: str = ""


@dataclass
class AmmeterAnalytics:
    run_id: str
    ammeter_type: str
    valid_sample_count: int
    failed_sample_count: int
    mean_current_a: Optional[float]
    median_current_a: Optional[float]
    standard_deviation_a: Optional[float]
    minimum_current_a: Optional[float]
    maximum_current_a: Optional[float]
    coefficient_of_variation: Optional[float]


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


class AmmeterTestFramework:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self._threads: List[threading.Thread] = []
        self._last_run_id: Optional[str] = None
        self._last_run_started_at: Optional[str] = None
        self._last_run_ended_at: Optional[str] = None

    @staticmethod
    def _load_ammeter_class(ammeter_config: Dict[str, Any]) -> Type[AmmeterEmulatorBase]:
        module = importlib.import_module(ammeter_config["module"])
        return getattr(module, ammeter_config["class"])

    @staticmethod
    def _command_to_bytes(command: Any) -> bytes:
        if isinstance(command, bytes):
            return command
        if isinstance(command, str):
            return command.encode("utf-8")
        raise TypeError(f"Ammeter command must be a string or bytes, got {type(command).__name__}.")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _csv_value(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(value, 10)

    def _sampling_settings(self) -> Dict[str, float]:
        sampling_config = self.config.get("testing", {}).get("sampling", {})
        duration_seconds = float(sampling_config.get("total_duration_seconds", 0) or 0)
        frequency_hz = float(sampling_config.get("sampling_frequency_hz", 0) or 0)

        if duration_seconds <= 0:
            raise ValueError("total_duration_seconds must be greater than zero.")
        if frequency_hz <= 0:
            raise ValueError("sampling_frequency_hz must be greater than zero.")

        return {
            "duration_seconds": duration_seconds,
            "frequency_hz": frequency_hz,
            "sample_count": math.ceil(duration_seconds * frequency_hz),
            "sample_interval_seconds": 1.0 / frequency_hz,
        }

    def start_emulators(self) -> None:
        """Start each ammeter emulator in a separate daemon thread using config."""
        for ammeter_type, ammeter_config in self.config["ammeters"].items():
            ammeter_class = self._load_ammeter_class(ammeter_config)
            port = ammeter_config["port"]
            ammeter = ammeter_class(port)

            thread = threading.Thread(
                target=ammeter.start_server,
                daemon=True,
                name=f"{ammeter_type}_emulator",
            )
            thread.start()
            self._threads.append(thread)

        time.sleep(5)

    def run_tests(self) -> List[MeasurementSample]:
        """Collect current measurements from every configured ammeter."""
        ammeters = self.config.get("ammeters", {})
        if not ammeters:
            raise ValueError("No ammeters are configured.")

        settings = self._sampling_settings()
        run_id = str(uuid.uuid4())
        samples: List[MeasurementSample] = []
        start_time = time.monotonic()

        self._last_run_id = run_id
        self._last_run_started_at = self._utc_now()
        self._last_run_ended_at = None

        for sample_index in range(1, int(settings["sample_count"]) + 1):
            scheduled_time = start_time + ((sample_index - 1) * settings["sample_interval_seconds"])
            remaining_time = scheduled_time - time.monotonic()
            if remaining_time > 0:
                time.sleep(remaining_time)

            for ammeter_type, ammeter_config in ammeters.items():
                timestamp_utc = self._utc_now()
                elapsed_seconds = time.monotonic() - start_time

                try:
                    current = request_current_from_ammeter(
                        port=int(ammeter_config["port"]),
                        command=self._command_to_bytes(ammeter_config["command"]),
                    )
                    samples.append(
                        MeasurementSample(
                            run_id=run_id,
                            sample_index=sample_index,
                            ammeter_type=ammeter_type,
                            timestamp_utc=timestamp_utc,
                            elapsed_seconds=elapsed_seconds,
                            current_a=current,
                            status="ok",
                        )
                    )
                except Exception as exc:
                    samples.append(
                        MeasurementSample(
                            run_id=run_id,
                            sample_index=sample_index,
                            ammeter_type=ammeter_type,
                            timestamp_utc=timestamp_utc,
                            elapsed_seconds=elapsed_seconds,
                            current_a=None,
                            status="error",
                            error=str(exc),
                        )
                    )

        self._last_run_ended_at = self._utc_now()
        return samples

    def analyze(self, measurements: List[MeasurementSample]) -> Dict[str, AmmeterAnalytics]:
        """Compute statistical metrics for each configured ammeter."""
        run_id = self._run_id_from_samples(measurements)
        analytics: Dict[str, AmmeterAnalytics] = {}

        for ammeter_type in self.config.get("ammeters", {}):
            ammeter_samples = [
                sample for sample in measurements if sample.ammeter_type == ammeter_type
            ]
            valid_values = [
                sample.current_a
                for sample in ammeter_samples
                if sample.status == "ok" and sample.current_a is not None
            ]
            failed_count = len(ammeter_samples) - len(valid_values)

            if valid_values:
                mean_current = statistics.mean(valid_values)
                standard_deviation = (
                    statistics.stdev(valid_values) if len(valid_values) > 1 else 0.0
                )
                coefficient_of_variation = (
                    standard_deviation / abs(mean_current) if mean_current != 0 else None
                )
                analytics[ammeter_type] = AmmeterAnalytics(
                    run_id=run_id,
                    ammeter_type=ammeter_type,
                    valid_sample_count=len(valid_values),
                    failed_sample_count=failed_count,
                    mean_current_a=mean_current,
                    median_current_a=statistics.median(valid_values),
                    standard_deviation_a=standard_deviation,
                    minimum_current_a=min(valid_values),
                    maximum_current_a=max(valid_values),
                    coefficient_of_variation=coefficient_of_variation,
                )
            else:
                analytics[ammeter_type] = AmmeterAnalytics(
                    run_id=run_id,
                    ammeter_type=ammeter_type,
                    valid_sample_count=0,
                    failed_sample_count=failed_count,
                    mean_current_a=None,
                    median_current_a=None,
                    standard_deviation_a=None,
                    minimum_current_a=None,
                    maximum_current_a=None,
                    coefficient_of_variation=None,
                )

        return analytics

    def save_results(
        self,
        measurements: List[MeasurementSample],
        analysis: Dict[str, AmmeterAnalytics],
    ) -> Path:
        """Archive per-ammeter samples, analytics, metadata, and plots for a completed run."""
        run_id = self._run_id_from_samples(measurements)
        output_dir = Path(self.config.get("result_management", {}).get("output_dir", "results"))
        run_dir = output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        sample_artifacts = {
            ammeter_type: f"{ammeter_type}_samples.csv"
            for ammeter_type in self.config.get("ammeters", {})
        }
        analytics_filename = "analytics.csv"
        metadata_filename = "metadata.json"
        timeseries_plot_filename = "current_timeseries.png"
        artifacts: Dict[str, Any] = {
            "samples": sample_artifacts,
            "analytics": analytics_filename,
            "metadata": metadata_filename,
            "timeseries_plot": timeseries_plot_filename,
        }

        for ammeter_type, sample_filename in sample_artifacts.items():
            ammeter_measurements = [
                sample for sample in measurements if sample.ammeter_type == ammeter_type
            ]
            self._write_samples_csv(run_dir / sample_filename, ammeter_measurements)

        self._write_analytics_csv(run_dir / analytics_filename, analysis)
        self._write_timeseries_plot(run_dir / timeseries_plot_filename, measurements)
        self._write_metadata_json(run_dir / metadata_filename, measurements, artifacts)

        return run_dir

    def _run_id_from_samples(self, measurements: List[MeasurementSample]) -> str:
        if measurements:
            return measurements[0].run_id
        if self._last_run_id:
            return self._last_run_id
        return str(uuid.uuid4())

    def _write_samples_csv(self, output_path: Path, measurements: List[MeasurementSample]) -> None:
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
                row["elapsed_seconds"] = self._csv_value(sample.elapsed_seconds)
                row["current_a"] = self._csv_value(sample.current_a)
                writer.writerow(row)

    def _write_analytics_csv(
        self,
        output_path: Path,
        analysis: Dict[str, AmmeterAnalytics],
    ) -> None:
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
            for ammeter_type in self.config.get("ammeters", {}):
                row = asdict(analysis[ammeter_type])
                row.pop("run_id")
                for fieldname in fieldnames:
                    if isinstance(row[fieldname], float):
                        row[fieldname] = self._csv_value(row[fieldname])
                writer.writerow(row)

    def _write_metadata_json(
        self,
        output_path: Path,
        measurements: List[MeasurementSample],
        artifacts: Dict[str, Any],
    ) -> None:
        total_samples = len(measurements)
        failed_samples = sum(1 for sample in measurements if sample.status != "ok")
        valid_samples = total_samples - failed_samples
        started_at = (
            self._last_run_started_at
            or (measurements[0].timestamp_utc if measurements else self._utc_now())
        )
        ended_at = (
            self._last_run_ended_at
            or (measurements[-1].timestamp_utc if measurements else self._utc_now())
        )

        metadata = TestRunMetadata(
            run_id=self._run_id_from_samples(measurements),
            started_at_utc=started_at,
            ended_at_utc=ended_at,
            status="completed" if failed_samples == 0 else "completed_with_errors",
            sampling_config=self.config.get("testing", {}).get("sampling", {}),
            ammeter_config=self.config.get("ammeters", {}),
            total_samples=total_samples,
            valid_samples=valid_samples,
            failed_samples=failed_samples,
            artifacts=artifacts,
        )

        with output_path.open("w", encoding="utf-8") as json_file:
            json.dump(asdict(metadata), json_file, indent=2)

    def _write_timeseries_plot(
        self,
        output_path: Path,
        measurements: List[MeasurementSample],
    ) -> None:
        fig, ax = plt.subplots(figsize=(10, 6))
        plotted = False

        for ammeter_type in self.config.get("ammeters", {}):
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

        ax.set_title("Current Measurements Over Time")
        ax.set_xlabel("Elapsed time (seconds)")
        ax.set_ylabel("Current (A)")
        ax.grid(True, alpha=0.3)
        if plotted:
            ax.legend()
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)