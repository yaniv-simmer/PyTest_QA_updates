import math
import time
import uuid
import importlib
import threading
import statistics

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from ..utils.logger import TestLogger
from ..utils.config import AmmeterConfig, AppConfig, load_config
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


@dataclass(frozen=True)
class SamplingPlan:
    duration_seconds: float
    frequency_hz: float
    sample_count: int
    interval_seconds: float
    request_timeout_seconds: float = 1.0


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
    def __init__(self, config_path: str):
        self.logger = TestLogger(test_name=__class__.__name__, log_dir=LOG_DIR)
        self.config: AppConfig = load_config(config_path, self.logger)
        self._threads: List[threading.Thread] = []
        self._last_run_id: Optional[str] = None
        self._last_run_started_at: Optional[str] = None
        self._last_run_ended_at: Optional[str] = None

    @staticmethod
    def _load_ammeter_class(ammeter_config: AmmeterConfig) -> Type[AmmeterEmulatorBase]:
        module = importlib.import_module(ammeter_config.module)
        return getattr(module, ammeter_config.class_name)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def start_emulators(self) -> None:
        """Start each ammeter emulator in a separate daemon thread using config."""
        try:
            self.logger.info("Starting configured ammeter emulators")
            for ammeter_type, ammeter_config in self.config.ammeters.items():
                ammeter_class = self._load_ammeter_class(ammeter_config)
                ammeter = ammeter_class(ammeter_config.port)

                thread = threading.Thread(
                    target=ammeter.start_server,
                    daemon=True,
                    name=f"{ammeter_type}_emulator",
                )
                thread.start()
                self._threads.append(thread)
                self.logger.info(f"Started {ammeter_type} emulator on port {ammeter_config.port}")
            time.sleep(5) # legacy code of original homework assignment
            self.logger.info("All emulators started successfully")
        except Exception as exc:
            self.logger.error(f"Failed to start emulators: {exc}")
            raise

    def _sampling_plan(self) -> SamplingPlan:
        sampling = self.config.sampling
        frequency_hz = sampling.sampling_frequency_hz
        return SamplingPlan(
            duration_seconds=sampling.total_duration_seconds,
            frequency_hz=frequency_hz,
            sample_count=math.ceil(sampling.total_duration_seconds * frequency_hz),
            interval_seconds=1.0 / frequency_hz,
        )

    def _begin_measurement_run(self, run_id: str, plan: SamplingPlan) -> None:
        self._last_run_id = run_id
        self._last_run_started_at = self._utc_now()
        self._last_run_ended_at = None
        self.logger.info(
            f"Starting measurement run {run_id}: "
            f"duration={plan.duration_seconds}s, frequency={plan.frequency_hz}Hz, "
            f"samples={plan.sample_count}, interval={plan.interval_seconds}s"
        )

    def _measure_ammeter(
        self,
        run_id: str,
        start_time: float,
        sample_index: int,
        ammeter_type: str,
        ammeter_config: AmmeterConfig,
        request_timeout_seconds: float,
    ) -> MeasurementSample:
        timestamp = self._utc_now()
        elapsed = time.monotonic() - start_time
        try:
            current = request_current_from_ammeter(
                port=ammeter_config.port,
                command=ammeter_config.command.encode("utf-8"),
                timeout_seconds=request_timeout_seconds,
            )
            status, error, current_a = "ok", "", current
        except Exception as exc:
            status, error, current_a = "error", str(exc), None
            self.logger.warning(
                f"Measurement failed for {ammeter_type} "
                f"on sample {sample_index}: {exc}"
            )

        return MeasurementSample(
            run_id=run_id,
            sample_index=sample_index,
            ammeter_type=ammeter_type,
            timestamp_utc=timestamp,
            elapsed_seconds=elapsed,
            current_a=current_a,
            status=status,
            error=error,
        )

    def _wait_for_sample_slot(
        self,
        run_id: str,
        start_time: float,
        sample_index: int,
        interval_seconds: float,
    ) -> None:
        scheduled = start_time + (sample_index - 1) * interval_seconds
        delay = scheduled - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -0.001:
            self.logger.warning(
                f"Measurement run {run_id}: sampling loop is late for sample "
                f"{sample_index} by {abs(delay):.4f}s"
            )

    def _collect_sample_batch(
        self,
        executor: ThreadPoolExecutor,
        run_id: str,
        start_time: float,
        sample_index: int,
        ammeters: Dict[str, AmmeterConfig],
        request_timeout_seconds: float,
    ) -> List[MeasurementSample]:
        future_to_ammeter = {
            executor.submit(
                self._measure_ammeter,
                run_id,
                start_time,
                sample_index,
                ammeter_type,
                ammeter_config,
                request_timeout_seconds,
            ): ammeter_type
            for ammeter_type, ammeter_config in ammeters.items()
        }
        sample_by_ammeter: Dict[str, MeasurementSample] = {}

        for future in as_completed(future_to_ammeter):
            ammeter_type = future_to_ammeter[future]
            sample_by_ammeter[ammeter_type] = future.result()

        return [sample_by_ammeter[ammeter_type] for ammeter_type in ammeters]

    def _finish_measurement_run(
        self, run_id: str, samples: List[MeasurementSample]
    ) -> None:
        self._last_run_ended_at = self._utc_now()
        failed = sum(1 for sample in samples if sample.status != "ok")
        self.logger.info(
            f"Measurement run {run_id} completed: "
            f"total_samples={len(samples)}, valid_samples={len(samples) - failed}, "
            f"failed_samples={failed}"
        )

    def run_tests(self) -> List[MeasurementSample]:
        """Collect current measurements from every configured ammeter."""
        ammeters: Dict[str, AmmeterConfig] = self.config.ammeters
        plan: SamplingPlan = self._sampling_plan()
        run_id = str(uuid.uuid4())
        self._begin_measurement_run(run_id, plan)

        start_time = time.monotonic()
        samples: List[MeasurementSample] = []

        with ThreadPoolExecutor(max_workers=len(ammeters)) as executor:
            for sample_index in range(1, plan.sample_count + 1):
                self._wait_for_sample_slot(
                    run_id, start_time, sample_index, plan.interval_seconds
                )
                batch = self._collect_sample_batch(
                    executor,
                    run_id,
                    start_time,
                    sample_index,
                    ammeters,
                    plan.request_timeout_seconds,
                )
                samples.extend(batch)

        self._finish_measurement_run(run_id, samples)
        return samples

    def analyze(self, measurements: List[MeasurementSample]) -> Dict[str, AmmeterAnalytics]:
        """Compute statistical metrics for each configured ammeter."""
        run_id = self._run_id_from_samples(measurements)
        self.logger.info(
            f"Starting analysis for run {run_id} with {len(measurements)} samples"
        )
        analytics: Dict[str, AmmeterAnalytics] = {}

        for ammeter_type in self.config.ammeters:
            ammeter_samples = [
                sample for sample in measurements if sample.ammeter_type == ammeter_type
            ]
            valid_values = [
                sample.current_a
                for sample in ammeter_samples
                if sample.status == "ok" and sample.current_a is not None
            ]
            if valid_values:
                mean_current = statistics.mean(valid_values)
                standard_deviation = (
                    statistics.stdev(valid_values) if len(valid_values) > 1 else 0.0
                )
                coefficient_of_variation = (
                    standard_deviation / abs(mean_current) if mean_current != 0 else None
                )
                median_current = statistics.median(valid_values)
                minimum_current = min(valid_values)
                maximum_current = max(valid_values)
            else:
                mean_current = median_current = standard_deviation = None
                minimum_current = maximum_current = coefficient_of_variation = None

            analytics[ammeter_type] = AmmeterAnalytics(
                run_id=run_id,
                ammeter_type=ammeter_type,
                valid_sample_count=len(valid_values),
                failed_sample_count=len(ammeter_samples) - len(valid_values),
                mean_current_a=mean_current,
                median_current_a=median_current,
                standard_deviation_a=standard_deviation,
                minimum_current_a=minimum_current,
                maximum_current_a=maximum_current,
                coefficient_of_variation=coefficient_of_variation,
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