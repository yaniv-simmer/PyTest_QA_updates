import pandas as pd

from src.testing.test_framework import AmmeterTestFramework


def main():
    framework = AmmeterTestFramework("config/config.yaml")

    framework.start_emulators()
    measurements_df: pd.DataFrame = framework.run_tests()
    analysis_df: pd.DataFrame = framework.analyze(measurements_df)
    framework.save_results(measurements_df, analysis_df)

if __name__ == "__main__":
    main()
