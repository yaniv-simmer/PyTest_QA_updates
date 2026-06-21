from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Optional, Type

import pandas as pd

# --- Config models ---


@dataclass
class SamplingConfig:
    measurements_count: Optional[int]
    total_duration_seconds: float
    sampling_frequency_hz: float

    def __post_init__(self) -> None:
        if self.total_duration_seconds <= 0:
            raise ValueError("total_duration_seconds must be greater than zero.")
        if self.sampling_frequency_hz <= 0:
            raise ValueError("sampling_frequency_hz must be greater than zero.")


@dataclass
class AmmeterConfig:
    class_name: str
    module: str
    port: int
    command: str


@dataclass
class AppConfig:
    sampling: SamplingConfig
    ammeters: Dict[str, AmmeterConfig]
    output_dir: str

    def __post_init__(self) -> None:
        if not self.ammeters:
            raise ValueError("No ammeters are configured.")


# --- Measurement models ---


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


# --- Column constants ---

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

MEASUREMENT_COLUMNS = [
    RUN_ID_COLUMN,
    "sample_index",
    AMMETER_TYPE_COLUMN,
    TIMESTAMP_UTC_COLUMN,
    "elapsed_seconds",
    CURRENT_COLUMN,
    STATUS_COLUMN,
    "error",
]

SAMPLE_CSV_COLUMNS = [
    "sample_index",
    AMMETER_TYPE_COLUMN,
    TIMESTAMP_UTC_COLUMN,
    "elapsed_seconds",
    CURRENT_COLUMN,
    STATUS_COLUMN,
    "error",
]

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

ANALYTICS_CSV_COLUMNS = [
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

ANALYTICS_FLOAT_COLUMNS = [
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

HISTORICAL_SAMPLE_COLUMNS = [RUN_ID_COLUMN, AMMETER_TYPE_COLUMN, STATUS_COLUMN, CURRENT_COLUMN]

HISTORICAL_ANALYTICS_COLUMNS = [
    AMMETER_TYPE_COLUMN,
    "source_run_count",
    VALID_SAMPLE_COUNT_COLUMN,
    FAILED_SAMPLE_COUNT_COLUMN,
    MEAN_CURRENT_COLUMN,
    MEDIAN_CURRENT_COLUMN,
    STANDARD_DEVIATION_COLUMN,
    MINIMUM_CURRENT_COLUMN,
    MAXIMUM_CURRENT_COLUMN,
    "current_range_a",
    COEFFICIENT_OF_VARIATION_COLUMN,
    "stability_rank",
]

HISTORICAL_FLOAT_COLUMNS = [
    MEAN_CURRENT_COLUMN,
    MEDIAN_CURRENT_COLUMN,
    STANDARD_DEVIATION_COLUMN,
    MINIMUM_CURRENT_COLUMN,
    MAXIMUM_CURRENT_COLUMN,
    "current_range_a",
    COEFFICIENT_OF_VARIATION_COLUMN,
]

HISTORICAL_COUNT_COLUMNS = [
    "source_run_count",
    VALID_SAMPLE_COUNT_COLUMN,
    FAILED_SAMPLE_COUNT_COLUMN,
    "stability_rank",
]


# --- DataFrame helpers ---


def dataclass_field_names(model: Type[Any]) -> List[str]:
    return [field.name for field in fields(model)]


def normalize_measurements_dataframe(measurements_df: pd.DataFrame) -> pd.DataFrame:
    measurements_df = measurements_df.reindex(columns=MEASUREMENT_COLUMNS).copy()
    for column in ("elapsed_seconds", CURRENT_COLUMN):
        measurements_df[column] = pd.to_numeric(measurements_df[column], errors="coerce")
    return measurements_df


def normalize_analysis_dataframe(analysis_df: pd.DataFrame) -> pd.DataFrame:
    analysis_df = analysis_df.reindex(columns=ANALYTICS_CSV_COLUMNS).copy()
    for column in ANALYTICS_FLOAT_COLUMNS:
        analysis_df[column] = pd.to_numeric(analysis_df[column], errors="coerce")
    return analysis_df


def measurements_to_dataframe(measurements: List[MeasurementSample]) -> pd.DataFrame:
    measurements_df = pd.DataFrame(
        data=[asdict(sample) for sample in measurements],
        columns=dataclass_field_names(MeasurementSample),
    )
    return normalize_measurements_dataframe(measurements_df)
