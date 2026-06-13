from src.testing.test_framework import AmmeterTestFramework


def main():
    framework = AmmeterTestFramework("config/config.yaml")

    framework.start_emulators()
    measurements = framework.run_tests()
    analysis = framework.analyze(measurements)
    result_path = framework.save_results(measurements, analysis)
    print(f"Test run saved to: {result_path}")

if __name__ == "__main__":
    main()