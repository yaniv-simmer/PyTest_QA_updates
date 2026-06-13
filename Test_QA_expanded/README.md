# Ammeter Test Framework

This project is a Python-based testing framework for simulated current measurement devices. It was built for an embedded systems QA exercise and demonstrates a unified way to start multiple ammeter emulators, collect timed current samples, analyze the results, and archive each run for later comparison.

Built with `Python 3.12.10`

## Table of Contents

- [Application Flow](#application-flow)
- [Overview](#overview)
- [Project Structure](#project-structure)
- [Setup and Usage](#setup-and-usage)
- [Configuration-Driven Design](#configuration-driven-design)
- [Results and Analytics](#results-and-analytics)
- [Design Decisions](#design-decisions)

## Application Flow

The application flow is:

1. [main.py](main.py) creates an `AmmeterTestFramework` instance using [config/config.yaml](config/config.yaml).
2. The framework loads and validates the configuration.
3. Each configured ammeter class is dynamically imported from its module path.
4. Every configured emulator is started in its own daemon thread.
5. The framework calculates a sampling plan from the configured duration and frequency.
6. On each sample cycle, the framework queries all configured ammeters through the socket client.
7. Each measurement is stored with run ID, sample index, ammeter type, timestamp, elapsed time, current value, status, and error text.
8. Measurements are collected into a pandas DataFrame.
9. The analysis step computes per-ammeter statistics.
10. The result writer archives the run under a unique UUID.
11. Historical analytics are regenerated from all saved sample runs.

## Overview

The assignment asks for a comprehensive test framework for multiple current measurement systems. This implementation provides:

- A unified interface for running different ammeter emulators.
- Configurable sampling duration and frequency.
- Per-sample status and error tracking.
- Statistical analysis for each ammeter.
- CSV, JSON, plot, log, and historical analytics output.
- A configuration-driven architecture so new ammeters can be added with minimal framework changes.

## Project Structure

```text
Test_QA_expanded/
|-- Ammeters/ : Ammeter emulator implementations and socket communication code.
|   |-- base_ammeter.py : Abstract base class that defines the emulator interface.
|   |-- client.py : Socket client used to request measurements from emulator servers.
|   |-- Greenlee_Ammeter.py : Greenlee emulator using Ohm's Law style measurement logic.
|   |-- Entes_Ammeter.py : ENTES emulator using Hall Effect style measurement logic.
|   `-- Circutor_Ammeter.py : CIRCUTOR emulator using Rogowski coil style integration.
|-- config/ : Runtime configuration files.
|   `-- config.yaml : Sampling settings, ammeter registration, commands, ports, and output path.
|-- Exam/ : Original assignment material.
|   |-- ammeter-test-specification.md : Markdown version of the assignment requirements.
|   `-- ammeter-test-specification.pdf : PDF version of the assignment requirements.
|-- examples/ : Optional example scripts.
|   `-- run_tests.py : Example runner for experimenting with the framework.
|-- results/ : Generated output from framework executions.
|   |-- analytics/ : Historical cross-run analytics and dashboard output.
|   |-- logs/ : Framework log files.
|   `-- samples/ : Per-run archived samples, metadata, analytics, and plots.
|-- src/ : Main framework implementation.
|   |-- testing/ : Test orchestration logic.
|   |   `-- test_framework.py : Loads config, starts emulators, samples data, analyzes, and saves results.
|   `-- utils/ : Supporting utilities for configuration, logging, output, and analytics.
|       |-- accuracy_assessment.py : Builds historical stability analytics across saved runs.
|       |-- config.py : Loads and validates YAML config into structured application settings.
|       |-- logger.py : Creates timestamped framework logs.
|       |-- test_results.py : Writes run CSV files, metadata JSON, and plots.
|       `-- Utils.py : Shared helper functions used by the emulators.
|-- main.py : Main entry point for running the complete test flow.
|-- requirements.txt : Python dependencies required by the project.
`-- README.md : Project documentation.
```

## Setup and Usage

From the repository root, create and activate a virtual environment, then install dependencies from [requirements.txt](requirements.txt):

```powershell
cd Test_QA_expanded
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the full framework:

```powershell
python main.py
```

On macOS or Linux, use `source .venv/bin/activate` instead of the Windows activation command.

## Configuration-Driven Design

The framework is driven by [config/config.yaml](config/config.yaml). The configuration defines:

- Sampling behavior:
  - `total_duration_seconds`: Total time to collect samples.
  - `sampling_frequency_hz`: Number of sample cycles per second.
  - `measurements_count`: Optional configuration field retained in the schema. The current sampler derives the actual sample count from duration and frequency.
- Ammeter registration:
  - Logical ammeter name.
  - Python module path.
  - Emulator class name.
  - TCP port.
  - Measurement command.
- Result management:
  - Output directory.

Example ammeter configuration:

```yaml
ammeters:
  greenlee:
    class: GreenleeAmmeter
    module: Ammeters.Greenlee_Ammeter
    port: 5000
    command: "MEASURE_GREENLEE -get_measurement"
```

Because the framework dynamically imports the configured module and class, the core test runner does not need to know about each ammeter in advance. This keeps the test framework reusable and makes the supported device list easy to extend.

To add a new ammeter emulator:

1. Create a new emulator class under [Ammeters/](Ammeters/).
2. Inherit from `AmmeterEmulatorBase`.
3. Implement `get_current_command`.
4. Implement `measure_current`.
5. Add the ammeter to [config/config.yaml](config/config.yaml) with a unique name, module, class, port, and command.

Example:

```yaml
ammeters:
  new_meter:
    class: NewMeterAmmeter
    module: Ammeters.NewMeter_Ammeter
    port: 5003
    command: "MEASURE_NEW_METER -get_measurement"
```

After that, [main.py](main.py) can run the new ammeter together with the existing devices without changing the framework core.

## Results and Analytics

Each execution creates a unique run directory under [results/samples/](results/samples/):

```text
results/samples/<run_id>/
```

That directory contains:

- [metadata.json](results/samples/631c0bce-245e-4d5d-a0e7-365e5c8c27ab/metadata.json): Run ID, UTC timestamps, run status, sampling configuration, ammeter configuration, total samples, valid samples, failed samples, and artifact paths.
- [greenlee/greenlee_samples.csv](results/samples/631c0bce-245e-4d5d-a0e7-365e5c8c27ab/greenlee/greenlee_samples.csv): Raw sample data for each ammeter. Other ammeters get the same file pattern.
- [greenlee/time_series.png](results/samples/631c0bce-245e-4d5d-a0e7-365e5c8c27ab/greenlee/time_series.png): Per-ammeter plot of current measurements over time. Other ammeters get the same file pattern.
- [analytics/analytics.csv](results/samples/631c0bce-245e-4d5d-a0e7-365e5c8c27ab/analytics/analytics.csv): Per-run statistics for each ammeter.
- [analytics/time_series.png](results/samples/631c0bce-245e-4d5d-a0e7-365e5c8c27ab/analytics/time_series.png): Combined time-series plot for all configured ammeters.

The per-run analytics include:

- Valid sample count.
- Failed sample count.
- Mean current.
- Median current.
- Standard deviation.
- Minimum current.
- Maximum current.
- Coefficient of variation.

The project also maintains historical analytics in [results/analytics/](results/analytics/):

```text
results/analytics/
```

This folder contains:

- [accuracy_assessment_analytics.csv](results/analytics/accuracy_assessment_analytics.csv): Historical statistics across all saved runs.
- [accuracy_assessment_dashboard.png](results/analytics/accuracy_assessment_dashboard.png): Visual dashboard comparing historical current distribution and relative stability.

The historical ranking is based on measurement stability, using the coefficient of variation. Lower coefficient of variation means the ammeter produced more stable repeated measurements. This is not a true absolute accuracy ranking because the emulator setup does not provide a shared reference current.

Logs are written to [results/logs/](results/logs/):

```text
results/logs/
```

## Design Decisions

- Configuration-driven architecture: Sampling behavior, emulator registration, commands, ports, and output location are controlled from YAML to reduce code changes.
- Dynamic imports: The framework loads ammeter classes from configuration, making new emulators easy to register.
- Unified socket client: All ammeters are queried through the same request path, even though their internal measurement logic differs.
- Threaded emulators: Each emulator runs in its own thread to simulate multiple devices being available at the same time.
- DataFrame-based analysis: pandas provides clear grouping, statistics, CSV export, and future analysis flexibility.
- Run archival with UUIDs: Every run is stored separately, making results reproducible, inspectable, and comparable over time.
- Historical analytics: Saved sample files are reused to compare stability across all previous runs.
- Minimal focused dependencies: External packages are limited to configuration loading, data handling, numerical work, and plotting.
