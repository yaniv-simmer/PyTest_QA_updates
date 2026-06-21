import importlib
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Type

import pandas as pd

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.client import request_current_from_ammeter

from ..utils.config import load_config
from ..utils.logger import TestLogger
from .historical_accuracy_assessor import HistoricalAccuracyAssessor
from .models import (
    AmmeterConfig,
    ANALYTICS_AGGREGATIONS,
    ANALYTICS_COLUMNS,
    AMMETER_TYPE_COLUMN,
    AppConfig,
    COEFFICIENT_OF_VARIATION_COLUMN,
    CURRENT_COLUMN,
    FAILED_SAMPLE_COUNT_COLUMN,
    MeasurementSample,
    MEAN_CURRENT_COLUMN,
    normalize_measurements_dataframe,
    measurements_to_dataframe,
    RUN_ID_COLUMN,
    STANDARD_DEVIATION_COLUMN,
    STATUS_COLUMN,
    STATUS_ERROR,
    STATUS_OK,
    TIMESTAMP_UTC_COLUMN,
    VALID_SAMPLE_COUNT_COLUMN,
)
from .results_writer import TestResultsWriter

LOG_DIR = "results/logs"


class AmmeterTestFramework:
    def __init__(self, config_path: str):
        self.logger = TestLogger(test_name=__class__.__name__, log_dir=LOG_DIR)
        self.config: AppConfig = load_config(config_path, self.logger)
        self._threads: List[threading.Thread] = []
        self._last_run_id: Optional[str] = None
        self._last_run_started_at: Optional[str] = None
        self._last_run_ended_at: Optional[str] = None
        self._results_writer = TestResultsWriter()
        self._historical_accuracy_assessor = HistoricalAccuracyAssessor()

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
            time.sleep(5)  # legacy code of original homework assignment
            self.logger.info("All emulators started successfully")
        except Exception as exc:
            self.logger.error(f"Failed to start emulators: {exc}")
            raise

    def _begin_measurement_run(self, run_id: str) -> None:
        self._last_run_id = run_id
        self._last_run_started_at = self._utc_now()
        self._last_run_ended_at = None
        self.logger.info(
            f"Starting measurement run {run_id}"
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
        """Collect a batch of samples from all configured ammeters."""
        futures = [
            executor.submit(
                self._measure_ammeter,
                run_id,
                start_time,
                sample_index,
                ammeter_type,
                ammeter_config,
                request_timeout_seconds,
            )
            for ammeter_type, ammeter_config in ammeters.items()
        ]
        return [future.result() for future in futures]

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
        sampling = self.config.sampling
        frequency_hz = sampling.sampling_frequency_hz
        sample_count = math.ceil(sampling.total_duration_seconds * frequency_hz)
        interval_seconds = 1.0 / frequency_hz
        run_id = str(uuid.uuid4())
        self._begin_measurement_run(run_id)

        start_time = time.monotonic()
        samples: List[MeasurementSample] = []

        with ThreadPoolExecutor(max_workers=len(ammeters)) as executor:
            for sample_index in range(1, sample_count + 1):
                self._wait_for_sample_slot(
                    run_id, start_time, sample_index, interval_seconds
                )
                batch = self._collect_sample_batch(
                    executor,
                    run_id,
                    start_time,
                    sample_index,
                    ammeters,
                    1.0,
                )
                samples.extend(batch)

        self._finish_measurement_run(run_id, samples)
        return measurements_to_dataframe(samples)

    def analyze_run(self, measurements_df: pd.DataFrame) -> pd.DataFrame:
        """Compute statistical metrics for each configured ammeter."""
        measurements_df = normalize_measurements_dataframe(measurements_df)
        run_id = self._run_id_from_measurements(measurements_df)
        self.logger.info(
            f"Starting analysis for run {run_id} with {len(measurements_df)} samples"
        )
        ammeter_types = list(self.config.ammeters)
        valid_measurements_df = measurements_df[
            measurements_df[STATUS_COLUMN].eq(STATUS_OK)
            & measurements_df[CURRENT_COLUMN].notna()
        ]

        analytics_df = (
            valid_measurements_df.groupby(AMMETER_TYPE_COLUMN)[CURRENT_COLUMN]
            .agg(**ANALYTICS_AGGREGATIONS)
            .reindex(ammeter_types)
        )
        total_counts = (
            measurements_df.groupby(AMMETER_TYPE_COLUMN)
            .size()
            .reindex(ammeter_types, fill_value=0)
        )

        analytics_df[RUN_ID_COLUMN] = run_id
        analytics_df[VALID_SAMPLE_COUNT_COLUMN] = (
            analytics_df[VALID_SAMPLE_COUNT_COLUMN].fillna(0).astype(int)
        )
        analytics_df[FAILED_SAMPLE_COUNT_COLUMN] = (
            total_counts.astype(int) - analytics_df[VALID_SAMPLE_COUNT_COLUMN]
        )

        single_sample = analytics_df[VALID_SAMPLE_COUNT_COLUMN] == 1
        analytics_df.loc[single_sample, STANDARD_DEVIATION_COLUMN] = 0.0

        mean_abs = analytics_df[MEAN_CURRENT_COLUMN].abs()
        analytics_df[COEFFICIENT_OF_VARIATION_COLUMN] = (
            analytics_df[STANDARD_DEVIATION_COLUMN] / mean_abs
        ).where(mean_abs.ne(0) & analytics_df[MEAN_CURRENT_COLUMN].notna())

        analytics_df = analytics_df.reset_index().reindex(columns=ANALYTICS_COLUMNS)
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
        """Archive run results to the output directory."""
        measurements_df = normalize_measurements_dataframe(measurements_df)
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
            result_path = self._results_writer.save(
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

    def analyze_historical_cross_ammeter_accuracy_assessment(self) -> None:
        """Update historical cross-ammeter accuracy assessment artifacts."""
        output_dir = Path(self.config.output_dir)
        self.logger.info(f"Updating historical accuracy assessment in {output_dir}")
        try:
            self._historical_accuracy_assessor.write(output_dir)
        except Exception as exc:
            self.logger.error(
                f"Failed to update historical accuracy assessment in {output_dir}: {exc}"
            )
            raise
        self.logger.info("Historical accuracy assessment updated")

    def _run_id_from_measurements(self, measurements_df: pd.DataFrame) -> str:
        if not measurements_df.empty:
            return str(measurements_df.iloc[0][RUN_ID_COLUMN])
        if self._last_run_id:
            return self._last_run_id
        return str(uuid.uuid4())
