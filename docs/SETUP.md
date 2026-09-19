# Complete setup and configuration

This guide covers installation, Google Sheets access, local configuration, and
the shared controls created inside the spreadsheet.

## 1. Install Python and the project

Python 3.10 or later is recommended.

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

On Linux or macOS:

```bash
sh setup.sh
```

Both installers create a local `.venv`, install the project and its
dependencies, and create `config.json` from `config.example.json` when needed.
They never overwrite an existing local configuration.

For a manual installation:

```bash
python -m venv .venv
python -m pip install --editable .
```

## 2. Prepare Google Sheets

The tracker uses a Google service account. A service account is a separate
non-human Google identity used only by the application. It does not use your
Google password and does not require an OAuth consent screen.

### 2.1 Create or select a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Use the project selector at the top of the page.
3. Select an existing project or click **New Project**.
4. Keep that project selected during the following steps.

The Google Cloud project hosts the API identity. The spreadsheet can still
belong to your normal Google account.

### 2.2 Enable the Google Sheets API

1. Open **APIs & Services > Library** in Google Cloud Console.
2. Search for **Google Sheets API**.
3. Open it and click **Enable**.

Only the Google Sheets API is required. The Google Drive API is not required,
because the tracker opens one spreadsheet by its known ID and receives access
through normal spreadsheet sharing.

### 2.3 Create the service account

1. Open **IAM & Admin > Service Accounts**.
2. Click **Create service account**.
3. Enter a name such as `hytale-benchmark-tracker`.
4. Click **Create and continue**, then **Done**.

Do not grant the service account a project role such as Owner or Editor.
Project-level IAM permissions do not provide access to the spreadsheet. The
spreadsheet is shared separately below.

Copy the new service account email address. It looks like:

```text
hytale-benchmark-tracker@your-project-id.iam.gserviceaccount.com
```

### 2.4 Download a JSON key

1. Open the service account.
2. Open its **Keys** tab.
3. Select **Add key > Create new key**.
4. Select **JSON**, then click **Create**.
5. Move the downloaded file to a secure local location.

Google provides the private key file only when it is created. Treat it like a
password: never send it to another user, upload it to GitHub, paste it into
Google Sheets, or commit it to the repository. Each installation should
preferably have a separate service account or key so access can be revoked
independently.

If a key is exposed, delete it from the service account's **Keys** tab and
create a replacement. Organization-managed accounts may disable key creation;
in that case, contact the Google Workspace or Cloud administrator.

### 2.5 Create and share the spreadsheet

1. Create a blank spreadsheet in [Google Sheets](https://sheets.google.com/).
2. Click **Share**.
3. Enter the service account email address.
4. Select **Editor** and click **Share**.

The service account is a different identity from your personal Google account.
The spreadsheet can remain **Restricted** under General access; it does not
need to be public or shared as "Anyone with the link."

### 2.6 Copy the spreadsheet ID

The spreadsheet ID is the value between `/d/` and `/edit` in its URL:

```text
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
                                     ^^^^^^^^^^^^^^
```

The tracker accepts either this ID or the complete Google Sheets URL.

Official references:

- [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis)
- [Create a service account](https://cloud.google.com/iam/docs/service-accounts-create)
- [Create and manage service account keys](https://cloud.google.com/iam/docs/keys-create-delete)
- [Share a spreadsheet](https://support.google.com/a/users/answer/13309904)

## 3. Configure the local connection

Copy `config.example.json` to `config.json` if the installer has not already
done so. Never commit `config.json` or the service account key.

Important local settings:

- `service_account_json`: path to the downloaded JSON key;
- `spreadsheet_id`: spreadsheet ID or complete Google Sheets URL;
- `log_folder_override`: optional local path to the log folder;
- `state_file`: local file that stores reading positions.

Windows paths can use forward slashes or escaped backslashes:

```json
{
  "service_account_json": "C:/Users/YourName/Documents/Secrets/hytale-tracker.json",
  "spreadsheet_id": "SPREADSHEET_ID"
}
```

This example only shows the two Google connection fields. Keep all other
settings from `config.example.json` in the real file.

By default, `log_folder_override` is empty. The `hytale_prerelease` profile
automatically detects `%APPDATA%\Hytale\data\pre-release\Logs` for each Windows
user. Set the override only for a non-standard installation. On Linux or macOS,
set it to the local Hytale log directory because `%APPDATA%` is unavailable.

Local paths and credentials are never copied to Google Sheets. Legacy
configurations containing `log_folder` remain supported as a local override.

Before connecting to Google, the application validates the JSON syntax,
required values, credential file, spreadsheet ID, log directory, file patterns,
sample counts, and polling intervals.

## 4. Configure collection in Google Sheets

On the first run, the tracker creates a `Config` sheet with shared,
non-sensitive settings:

- `collection_enabled`: enables data collection while checked;
- `test_mode`: marks new benchmarks as tests while checked;
- `resume_from`: earliest benchmark date and time to import;
- `log_profile`: safe local folder profile (`hytale_prerelease`);
- `log_files`: file names or local patterns such as `*.log`, separated by commas;
- `sample_counts`: accepted Sample Count values;
- `world_structures`: accepted World Structure names, or blank for all;
- `analysis_recent_days`: default recent-data window in analysis sheets;
- `poll_seconds`: log polling interval;
- `config_refresh_seconds`: Config reload interval, with a minimum of 8 seconds.

Recommended `resume_from` format:

```text
2026-09-10 18:30:00
```

When collection is disabled, reading positions do not advance. After it is
enabled again, benchmarks older than `resume_from` are skipped. This can exclude
an entire test period.

The Config sheet can change safe runtime settings, but it never writes values
back to `config.json`. Local paths and credentials always remain local.

## 5. Run and use the tracker

```bash
python log_to_sheets.py
```

The tracker populates:

- `Benchmark History`: one complete row per benchmark, including every metric
  and the raw benchmark block. This is the permanent history;
- one analysis sheet for every `WorldStructure Name`;
- `Benchmark Archive`: a lightweight index of benchmarks hidden from analysis,
  with a link to the complete history row;
- `Config`: shared runtime settings.

Each analysis benchmark has a description and an `Archive` checkbox. A green
title identifies a test benchmark. Checking `Archive` hides its analysis column
and adds it to `Benchmark Archive`. Clear `Archive` in `Benchmark History` or in
the analysis sheet to restore it and remove the archive index row.

`.log_watcher_state.json` stores the last reading position for every log file.
