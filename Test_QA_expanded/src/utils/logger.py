import logging
import os
from datetime import datetime


class TestLogger:
    def __init__(self, test_name: str, log_dir: str = "results/logs"):
        self._test_name = test_name
        self._log_dir = log_dir
        self.log_file_path = self._build_log_file_path()
        self.logger = self._setup_logger()

    def _build_log_file_path(self) -> str:
        os.makedirs(self._log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_test_name = self._test_name.replace(" ", "_")
        return os.path.join(self._log_dir, f"{timestamp}_{safe_test_name}.log")

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"test_{self._test_name}_{id(self)}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        file_handler = logging.FileHandler(self.log_file_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        logger.addHandler(file_handler)

        return logger

    def info(self, message: str):
        self.logger.info(message)

    def error(self, message: str):
        self.logger.error(message)

    def debug(self, message: str):
        self.logger.debug(message)

    def warning(self, message: str):
        self.logger.warning(message) 