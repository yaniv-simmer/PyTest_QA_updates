from typing import Dict, Optional

import yaml

from .logger import TestLogger


def load_config(config_path: str, logger: Optional[TestLogger] = None) -> Dict:
    config_logger = logger or TestLogger("config_loader")

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except OSError as exc:
        config_logger.error(f"Failed to read config file {config_path}: {exc}")
        raise
    except yaml.YAMLError as exc:
        config_logger.error(f"Failed to parse config file {config_path}: {exc}")
        raise

    config_logger.info(f"Config loaded from {config_path}")
    return config