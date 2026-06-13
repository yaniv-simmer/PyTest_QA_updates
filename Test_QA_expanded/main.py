from src.testing.test_framework import AmmeterTestFramework, MeasurementSample, AmmeterAnalytics
from typing import List, Dict


def main():
    framework = AmmeterTestFramework("config/config.yaml")

    framework.start_emulators()
    measurements: List[MeasurementSample] = framework.run_tests()
    analysis: Dict[str, AmmeterAnalytics] = framework.analyze(measurements)
    framework.save_results(measurements, analysis)

if __name__ == "__main__":
    main()