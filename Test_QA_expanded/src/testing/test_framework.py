import math
import time
import uuid
import importlib
import threading
import statistics

from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from ..utils.logger import TestLogger
from ..utils.config import load_config
from ..utils.test_results import save_test_results
from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.client import request_current_from_ammeter

LOG_DIR = "results/logs"


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


class AmmeterTestFramework:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.logger = TestLogger("ammeter_test_framework", LOG_DIR)
        self.logger.info(f"Loading test framework configuration from {config_path}")
        self.config = load_config(config_path, self.logger)
        self.logger.info("Test framework configuration loaded successfully")
        self._threads: List[threading.Thread] = []
        self._last_run_id: Optional[str] = None
        self._last_run_started_at: Optional[str] = None
        self._last_run_ended_at: Optional[str] = None

    @staticmethod
    def _load_ammeter_class(ammeter_config: Dict[str, Any]) -> Type[AmmeterEmulatorBase]:
        module = importlib.import_module(ammeter_config["module"])
        return getattr(module, ammeter_config["class"])

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _sampling_settings(self) -> Dict[str, float]:
        sampling_config = self.config.get("testing", {}).get("sampling", {})
        duration_seconds = float(sampling_config.get("total_duration_seconds", 0) or 0)
        frequency_hz = float(sampling_config.get("sampling_frequency_hz", 0) or 0)

        if duration_seconds <= 0:
            self.logger.error("Invalid sampling config: total_duration_seconds <= 0")
            raise ValueError("total_duration_seconds must be greater than zero.")
        if frequency_hz <= 0:
            self.logger.error("Invalid sampling config: sampling_frequency_hz <= 0")
            raise ValueError("sampling_frequency_hz must be greater than zero.")

        return {
            "duration_seconds": duration_seconds,
            "frequency_hz": frequency_hz,
            "sample_count": math.ceil(duration_seconds * frequency_hz),
            "sample_interval_seconds": 1.0 / frequency_hz,
        }

    def start_emulators(self) -> None:
        """Start each ammeter emulator in a separate daemon thread using config."""
        self.logger.info("Starting configured ammeter emulators")
        for ammeter_type, ammeter_config in self.config["ammeters"].items():
            try:
                ammeter_class = self._load_ammeter_class(ammeter_config)
                ammeter = ammeter_class(ammeter_config["port"])

                thread = threading.Thread(
                    target=ammeter.start_server,
                    daemon=True,
                    name=f"{ammeter_type}_emulator",
                )
                thread.start()
                self._threads.append(thread)
                self.logger.info(
                    f"Started {ammeter_type} emulator on port {ammeter_config['port']}"
                )
            except Exception as exc:
                self.logger.error(
                    f"Failed to start {ammeter_type} emulator: {exc}"
                )
                raise

        time.sleep(5)
        self.logger.info("Ammeter emulator startup wait completed")

    def run_tests(self) -> List[MeasurementSample]:
        """Collect current measurements from every configured ammeter."""
        ammeters = self.config.get("ammeters", {})
        if not ammeters:
            self.logger.error("No ammeters are configured")
            raise ValueError("No ammeters are configured.")

        settings = self._sampling_settings()
        run_id = str(uuid.uuid4())
        samples: List[MeasurementSample] = []
        start_time = time.monotonic()

        self._last_run_id = run_id
        self._last_run_started_at = self._utc_now()
        self._last_run_ended_at = None
        self.logger.info(
            "Starting measurement run "
            f"{run_id}: duration={settings['duration_seconds']}s, "
            f"frequency={settings['frequency_hz']}Hz, "
            f"samples={settings['sample_count']}, "
            f"interval={settings['sample_interval_seconds']}s"
        )

        for sample_index in range(1, int(settings["sample_count"]) + 1):
            scheduled_time = start_time + (
                (sample_index - 1) * settings["sample_interval_seconds"]
            )
            remaining_time = scheduled_time - time.monotonic()
            if remaining_time > 0:
                time.sleep(remaining_time)

            for ammeter_type, ammeter_config in ammeters.items():
                timestamp_utc = self._utc_now()
                elapsed_seconds = time.monotonic() - start_time

                try:
                    current = request_current_from_ammeter(
                        port=int(ammeter_config["port"]),
                        command=ammeter_config["command"].encode("utf-8"),
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
                    self.logger.warning(
                        f"Measurement failed for {ammeter_type} "
                        f"on sample {sample_index}: {exc}"
                    )

        self._last_run_ended_at = self._utc_now()
        failed_samples = sum(1 for sample in samples if sample.status != "ok")
        self.logger.info(
            f"Measurement run {run_id} completed: "
            f"total_samples={len(samples)}, "
            f"valid_samples={len(samples) - failed_samples}, "
            f"failed_samples={failed_samples}"
        )
        return samples

    def analyze(self, measurements: List[MeasurementSample]) -> Dict[str, AmmeterAnalytics]:
        """Compute statistical metrics for each configured ammeter."""
        run_id = self._run_id_from_samples(measurements)
        self.logger.info(
            f"Starting analysis for run {run_id} with {len(measurements)} samples"
        )
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

        summary = ", ".join(
            f"{ammeter_type}: valid={result.valid_sample_count}, "
            f"failed={result.failed_sample_count}"
            for ammeter_type, result in analytics.items()
        )
        self.logger.info(f"Analysis completed for run {run_id}: {summary}")
        return analytics

    def save_results(
        self,
        measurements: List[MeasurementSample],
        analysis: Dict[str, AmmeterAnalytics],
    ) -> Path:
        """Archive per-ammeter samples, analytics, metadata, and plots for a run."""
        started_at = (
            self._last_run_started_at
            or (measurements[0].timestamp_utc if measurements else self._utc_now())
        )
        ended_at = (
            self._last_run_ended_at
            or (measurements[-1].timestamp_utc if measurements else self._utc_now())
        )

        run_id = self._run_id_from_samples(measurements)
        self.logger.info(f"Saving results for run {run_id}")
        try:
            result_path = save_test_results(
                config=self.config,
                measurements=measurements,
                analysis=analysis,
                run_id=run_id,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
            )
        except Exception as exc:
            self.logger.error(f"Failed to save results for run {run_id}: {exc}")
            raise

        self.logger.info(f"Results for run {run_id} saved to {result_path}")
        return result_path

    def _run_id_from_samples(self, measurements: List[MeasurementSample]) -> str:
        if measurements:
            return measurements[0].run_id
        if self._last_run_id:
            return self._last_run_id
        return str(uuid.uuid4())