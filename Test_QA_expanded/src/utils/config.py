import yaml

from ..testing.models import AmmeterConfig, AppConfig, SamplingConfig
from .logger import TestLogger


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
