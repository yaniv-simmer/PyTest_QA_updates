import importlib
import json
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.client import request_current_from_ammeter
from ..utils.config import load_config


class AmmeterTestFramework:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self._threads: List[threading.Thread] = []

    @staticmethod
    def _load_ammeter_class(ammeter_config: Dict[str, Any]) -> Type[AmmeterEmulatorBase]:
        module = importlib.import_module(ammeter_config["module"])
        return getattr(module, ammeter_config["class"])

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