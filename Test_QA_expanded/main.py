from pandas import DataFrame

from src.testing.test_framework import AmmeterTestFramework


def main():
    framework = AmmeterTestFramework("config/config.yaml")

    framework.start_emulators()
    measurements_df: DataFrame = framework.run_tests()
    analysis_df: DataFrame = framework.analyze(measurements_df)
    framework.save_results_and_update_historical_accuracy_assessment(
        measurements_df,
        analysis_df,
    )

if __name__ == "__main__":
    main()
