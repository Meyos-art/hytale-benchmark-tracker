# Hytale Benchmark Tracker

Collect Hytale performance reports from local log files and publish complete
benchmark history, per-world analyses, and archive controls to Google Sheets.

## 1. Install Python

Python 3.10 or later is recommended.

## 2. Install the project

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

### Linux or macOS

```bash
sh setup.sh
```

Both installers create a local `.venv`, install the project and its
dependencies, and create `config.json` from `config.example.json` when needed.
They never overwrite an existing local configuration.

For a manual installation, run:

```bash
python -m venv .venv
python -m pip install --editable .
```

## 3. Prepare Google Sheets

1. Create a Google Cloud project.
2. Enable the Google Sheets API and Google Drive API.
3. Create a service account.
4. Download its JSON key.
5. Open your Google Sheet and share it with the service account email address as an Editor.
6. Copy the Google Sheet ID into `config.json`.

Example:

```text
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
                                     ^^^^^^^^^^^^^^
```

## 4. Configure the local connection

Copy `config.example.json` to `config.json`, then set the local connection
values. Never commit `config.json` or the service account key.

`config.json` stores the local settings required before connecting to Google:

- `log_folder_override`: optional local path to the log folder
- `service_account_json`: path to the Google service account JSON key
- `spreadsheet_id`: Google Sheet ID
- `state_file`: local file used to remember reading positions

By default, `log_folder_override` remains empty. The `hytale_prerelease`
profile automatically detects `%APPDATA%\Hytale\data\pre-release\Logs` for
each user. Set the override only for a non-standard installation.

On Linux or macOS, set `log_folder_override` to the local Hytale log directory,
because the Windows `%APPDATA%` location is not available.

These sensitive or machine-specific values are never copied to Google Sheets.
Legacy configurations containing `log_folder` remain supported as a local
override.

The application validates the JSON syntax, required values, Google credential
file, spreadsheet ID, log directory, file patterns, sample counts, and polling
intervals before connecting to Google. Configuration errors include an explicit
message describing what must be corrected.

## 5. Configure data collection in Google Sheets

On the first run, the script creates a `Config` sheet. It contains the shared,
non-sensitive settings:

- `collection_enabled`: checked when data collection is enabled
- `test_mode`: marks new benchmarks as tests while checked
- `resume_from`: earliest benchmark date and time to import
- `log_profile`: safe local folder profile (`hytale_prerelease`)
- `log_files`: file names or local patterns such as `*.log`, separated by commas
- `sample_counts`: accepted Sample Count values
- `world_structures`: allowed World Structure names; leave blank to accept all
- `analysis_recent_days`: default recent-data window used by analysis sheets
- `poll_seconds`: log polling interval
- `config_refresh_seconds`: interval for reloading the Config sheet (minimum 8 seconds)

Recommended `resume_from` format: `2026-09-10 18:30:00`.

When data collection is disabled, reading positions do not advance. After data
collection is enabled again, benchmarks older than `resume_from` are skipped.
This makes it possible to exclude an entire test period.

The Config sheet can change safe runtime settings, but it never writes values
back to `config.json`. Local paths and credentials always remain local.

## 6. Run the script

```bash
python log_to_sheets.py
```

The script populates:

- `Benchmark History`: one complete row per benchmark, including every extracted
  metric and the raw benchmark block. This is the permanent history, even when
  a benchmark is removed from analysis.
- One analysis sheet for each `WorldStructure Name`.
- `Benchmark Archive`: a lightweight index of benchmarks hidden from analysis,
  with `Timestamp`, `WorldStructure Name`, `Sample Count`, `Block Hash`,
  `Description`, and `Benchmark History Link` columns.
- `Config`: shared, non-sensitive runtime settings.

Each benchmark in an analysis sheet has a description and an `Archive`
checkbox. A green title identifies a test benchmark without adding another row.
Checking `Archive` hides the benchmark column and adds it to `Benchmark Archive`.
To restore it, clear `Archive` in `Benchmark History` or in the analysis sheet.
The corresponding row is then removed from `Benchmark Archive`.

Each archived row contains a clickable link to the exact complete benchmark row
in `Benchmark History`.

The `.log_watcher_state.json` file stores the last reading position for every
log file.

## Project structure

`log_to_sheets.py` remains the backward-compatible entry point. The application
code is split into focused modules:

- `constants.py`: parsing expressions, labels, and visual styles
- `config.py`: local configuration and the safe Config sheet
- `parser.py`: benchmark detection and metric extraction
- `google_api.py`: authentication, retries, and shared Google API helpers
- `history.py`: complete Benchmark History storage and formatting
- `analysis.py`: per-world analysis sheets, filters, and column formatting
- `archive.py`: archive index and synchronization of benchmark controls
- `watcher.py`: log file state, streaming, and processing orchestration

You can start the application with either command:

```bash
python log_to_sheets.py
python -m hytale_benchmark_tracker
hytale-benchmark-tracker
```
