from pandas import DataFrame

from src.testing.test_framework import AmmeterTestFramework


def main():
    framework = AmmeterTestFramework("config/config.yaml")

    framework.start_emulators()
    measurements_df: DataFrame = framework.run_tests()
    analysis_df: DataFrame = framework.analyze_run(measurements_df)
    framework.save_results(
        measurements_df,
        analysis_df,
    )
    framework.analyze_historical_cross_ammeter_accuracy_assessment()


if __name__ == "__main__":
    main()
