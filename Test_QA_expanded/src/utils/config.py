from dataclasses import dataclass
from typing import Dict, Optional

import yaml

from .logger import TestLogger


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


def load_config(config_path: str, logger: TestLogger) -> AppConfig:
    logger.info(f"Loading config from {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)

        sampling_data = raw["testing"]["sampling"]
        config = AppConfig(
            sampling=SamplingConfig(
                measurements_count=sampling_data["measurements_count"],
                total_duration_seconds=float(sampling_data["total_duration_seconds"]),
                sampling_frequency_hz=float(sampling_data["sampling_frequency_hz"]),
            ),
            ammeters={
                name: AmmeterConfig(
                    class_name=data["class"],
                    module=data["module"],
                    port=int(data["port"]),
                    command=data["command"],
                )
                for name, data in raw["ammeters"].items()
            },
            output_dir=raw["result_management"]["output_dir"],
        )

        logger.info(f"Config loaded from {config_path}")
        return config
    except OSError as exc:
        logger.error(f"Failed to read config file {config_path}: {exc}")
        raise
    except yaml.YAMLError as exc:
        logger.error(f"Failed to parse config file {config_path}: {exc}")
        raise
    except (KeyError, TypeError, ValueError) as exc:
        logger.error(f"Invalid config file {config_path}: {exc}")
        raise