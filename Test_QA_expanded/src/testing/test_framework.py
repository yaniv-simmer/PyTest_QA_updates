import math
import time
import uuid
import importlib
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

import pandas as pd

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


RUN_ID_COLUMN = "run_id"
AMMETER_TYPE_COLUMN = "ammeter_type"
TIMESTAMP_UTC_COLUMN = "timestamp_utc"
CURRENT_COLUMN = "current_a"
STATUS_COLUMN = "status"
STATUS_OK = "ok"
STATUS_ERROR = "error"

VALID_SAMPLE_COUNT_COLUMN = "valid_sample_count"
FAILED_SAMPLE_COUNT_COLUMN = "failed_sample_count"
MEAN_CURRENT_COLUMN = "mean_current_a"
MEDIAN_CURRENT_COLUMN = "median_current_a"
STANDARD_DEVIATION_COLUMN = "standard_deviation_a"
MINIMUM_CURRENT_COLUMN = "minimum_current_a"
MAXIMUM_CURRENT_COLUMN = "maximum_current_a"
COEFFICIENT_OF_VARIATION_COLUMN = "coefficient_of_variation"

ANALYTICS_COLUMNS = [
    RUN_ID_COLUMN,
    AMMETER_TYPE_COLUMN,
    VALID_SAMPLE_COUNT_COLUMN,
    FAILED_SAMPLE_COUNT_COLUMN,
    MEAN_CURRENT_COLUMN,
    MEDIAN_CURRENT_COLUMN,
    STANDARD_DEVIATION_COLUMN,
    MINIMUM_CURRENT_COLUMN,
    MAXIMUM_CURRENT_COLUMN,
    COEFFICIENT_OF_VARIATION_COLUMN,
]

ANALYTICS_AGGREGATIONS = {
    VALID_SAMPLE_COUNT_COLUMN: "count",
    MEAN_CURRENT_COLUMN: "mean",
    MEDIAN_CURRENT_COLUMN: "median",
    STANDARD_DEVIATION_COLUMN: "std",
    MINIMUM_CURRENT_COLUMN: "min",
    MAXIMUM_CURRENT_COLUMN: "max",
}


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
            status, error, current_a = STATUS_OK, "", current
        except Exception as exc:
            status, error, current_a = STATUS_ERROR, str(exc), None
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
        failed = sum(1 for sample in samples if sample.status != STATUS_OK)
        self.logger.info(
            f"Measurement run {run_id} completed: "
            f"total_samples={len(samples)}, valid_samples={len(samples) - failed}, "
            f"failed_samples={failed}"
        )

    def run_tests(self) -> pd.DataFrame:
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
        return _measurements_to_dataframe(samples)

    def analyze(self, measurements_df: pd.DataFrame) -> pd.DataFrame:
        """Compute statistical metrics for each configured ammeter."""
        measurements_df = _normalize_measurements_dataframe(measurements_df)
        run_id = self._run_id_from_measurements(measurements_df)
        self.logger.info(
            f"Starting analysis for run {run_id} with {len(measurements_df)} samples"
        )
        analytics_rows: List[Dict[str, Any]] = []

        valid_measurements_df = measurements_df[
            measurements_df[STATUS_COLUMN].eq(STATUS_OK)
            & measurements_df[CURRENT_COLUMN].notna()
        ]
        total_counts = measurements_df.groupby(AMMETER_TYPE_COLUMN).size()
        stats = valid_measurements_df.groupby(AMMETER_TYPE_COLUMN)[CURRENT_COLUMN].agg(
            **ANALYTICS_AGGREGATIONS
        )

        for ammeter_type in self.config.ammeters:
            sample_count = int(total_counts.get(ammeter_type, 0))
            if ammeter_type in stats.index:
                ammeter_stats = stats.loc[ammeter_type]
                valid_sample_count = int(ammeter_stats[VALID_SAMPLE_COUNT_COLUMN])
                mean_current = _optional_float(ammeter_stats[MEAN_CURRENT_COLUMN])
                standard_deviation = _optional_float(
                    ammeter_stats[STANDARD_DEVIATION_COLUMN]
                )
                if valid_sample_count == 1:
                    standard_deviation = 0.0
                coefficient_of_variation = (
                    standard_deviation / abs(mean_current)
                    if mean_current and standard_deviation is not None
                    else None
                )
                median_current = _optional_float(ammeter_stats[MEDIAN_CURRENT_COLUMN])
                minimum_current = _optional_float(ammeter_stats[MINIMUM_CURRENT_COLUMN])
                maximum_current = _optional_float(ammeter_stats[MAXIMUM_CURRENT_COLUMN])
            else:
                valid_sample_count = 0
                mean_current = median_current = standard_deviation = None
                minimum_current = maximum_current = coefficient_of_variation = None

            analytics_rows.append(
                {
                    RUN_ID_COLUMN: run_id,
                    AMMETER_TYPE_COLUMN: ammeter_type,
                    VALID_SAMPLE_COUNT_COLUMN: valid_sample_count,
                    FAILED_SAMPLE_COUNT_COLUMN: sample_count - valid_sample_count,
                    MEAN_CURRENT_COLUMN: mean_current,
                    MEDIAN_CURRENT_COLUMN: median_current,
                    STANDARD_DEVIATION_COLUMN: standard_deviation,
                    MINIMUM_CURRENT_COLUMN: minimum_current,
                    MAXIMUM_CURRENT_COLUMN: maximum_current,
                    COEFFICIENT_OF_VARIATION_COLUMN: coefficient_of_variation,
                }
            )

        analytics_df = pd.DataFrame(analytics_rows, columns=ANALYTICS_COLUMNS)
        summary = ", ".join(
            f"{row[AMMETER_TYPE_COLUMN]}: valid={row[VALID_SAMPLE_COUNT_COLUMN]}, "
            f"failed={row[FAILED_SAMPLE_COUNT_COLUMN]}"
            for _, row in analytics_df.iterrows()
        )
        self.logger.info(f"Analysis completed for run {run_id}: {summary}")
        return analytics_df

    def save_results(
        self,
        measurements_df: pd.DataFrame,
        analysis_df: pd.DataFrame,
    ) -> Path:
        """Archive per-ammeter samples, analytics, metadata, and plots for a run."""
        measurements_df = _normalize_measurements_dataframe(measurements_df)
        started_at = (
            self._last_run_started_at
            or (
                str(measurements_df.iloc[0][TIMESTAMP_UTC_COLUMN])
                if not measurements_df.empty
                else self._utc_now()
            )
        )
        ended_at = (
            self._last_run_ended_at
            or (
                str(measurements_df.iloc[-1][TIMESTAMP_UTC_COLUMN])
                if not measurements_df.empty
                else self._utc_now()
            )
        )

        run_id = self._run_id_from_measurements(measurements_df)
        self.logger.info(f"Saving results for run {run_id}")
        try:
            result_path = save_test_results(
                config=self.config,
                measurements_df=measurements_df,
                analysis_df=analysis_df,
                run_id=run_id,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
            )
        except Exception as exc:
            self.logger.error(f"Failed to save results for run {run_id}: {exc}")
            raise

        self.logger.info(f"Results for run {run_id} saved to {result_path}")
        return result_path

    def _run_id_from_measurements(self, measurements_df: pd.DataFrame) -> str:
        if not measurements_df.empty:
            return str(measurements_df.iloc[0][RUN_ID_COLUMN])
        if self._last_run_id:
            return self._last_run_id
        return str(uuid.uuid4())


def _measurements_to_dataframe(measurements: List[MeasurementSample]) -> pd.DataFrame:
    measurements_df = pd.DataFrame(
        [asdict(sample) for sample in measurements],
        columns=_dataclass_field_names(MeasurementSample),
    )
    return _normalize_measurements_dataframe(measurements_df)


def _normalize_measurements_dataframe(measurements_df: pd.DataFrame) -> pd.DataFrame:
    measurements_df = measurements_df.reindex(
        columns=_dataclass_field_names(MeasurementSample)
    ).copy()
    measurements_df[CURRENT_COLUMN] = pd.to_numeric(
        measurements_df[CURRENT_COLUMN], errors="coerce"
    )
    return measurements_df


def _dataclass_field_names(model: Type[Any]) -> List[str]:
    return [field.name for field in fields(model)]


def _optional_float(value: Any) -> Optional[float]:
    if pd.isna(value):
        return None
    return float(value)
