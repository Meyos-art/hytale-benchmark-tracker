# Troubleshooting

## Google connection

### API error 403 or "Google Sheets API has not been used"

Confirm that the Google Sheets API is enabled in the same Google Cloud project
as the service account. API activation can take a short time to propagate. The
Google Drive API is not required.

### SpreadsheetNotFound

Check the spreadsheet ID or URL. Share the spreadsheet directly with the
`client_email` contained in the service account JSON key.

### Permission denied

Give the service account **Editor** access to the spreadsheet. Viewer access is
not sufficient. A Google Cloud IAM role does not replace spreadsheet sharing.

### Invalid grant, invalid credentials, or signature error

The service account key may have been deleted, disabled, corrupted, or copied
incorrectly. Create a new JSON key, update `service_account_json`, and delete any
exposed or obsolete key.

### Service account key creation is unavailable

An organization policy may prohibit user-managed service account keys. Contact
the Google Workspace or Cloud administrator. The tracker cannot bypass that
policy.

### Google API rate limit

The tracker automatically retries temporary `429` and server errors with
exponential backoff. Avoid running multiple tracker instances against the same
spreadsheet and do not set `config_refresh_seconds` below 8 seconds.

## Local configuration

### Service account JSON file not found

Check `service_account_json` in `config.json`. Relative paths are resolved from
the directory containing `config.json`. On Windows, prefer forward slashes or
escape every backslash in JSON.

### No file matches log_folder or log_files

Check that:

- `log_folder_override` points to an existing directory when set;
- `log_files` contains the correct file name or a pattern such as `*.log`;
- the target file ends in `.log`;
- the account running the tracker can read the directory and file.

With the default Windows profile, leave `log_folder_override` empty to use:

```text
%APPDATA%\Hytale\data\pre-release\Logs
```

Settings changed in the Google `Config` sheet do not write back to local
`config.json`. Local credentials and folder overrides must be changed locally.

### Config refresh interval error

`config_refresh_seconds` must be at least 8 seconds. Larger values reduce Google
API usage, while smaller values make shared setting changes appear sooner.

## Log reading state

### A changed log file is not read from the beginning

`.log_watcher_state.json` remembers the last byte position for each log file.
Stop the tracker before changing this file. Removing the entry for one log makes
that file eligible to be read again, which can re-import benchmarks unless they
are already recognized as duplicates.

### PermissionError while replacing .log_watcher_state.json

Make sure only one tracker instance is running and that no editor or sync tool
is locking the state file. The project directory must be writable by the current
user.

### Duplicate benchmarks

Do not run multiple tracker instances against the same logs and spreadsheet.
Keep `.log_watcher_state.json` between normal runs. Deleting it removes local
reading positions, although benchmark hashes still help prevent duplicate data.

## Spreadsheet behavior

### Checkboxes display TRUE or FALSE as text

Set the spreadsheet locale to **English (United States)**. The tracker also
enforces the `en_US` locale when it connects. If old cells remain plain text,
restart the tracker so it can repair their validation and values.

### An archived benchmark is missing from Benchmark Archive

Confirm the benchmark still exists as a complete row in `Benchmark History` and
that its `Archive` value is checked. Restart the tracker to synchronize analysis,
history, and the lightweight archive index.

### Starting with a blank spreadsheet

Use a truly blank spreadsheet and let the tracker create its own worksheets.
Do not pre-create partial sheets with fewer columns under the reserved names
`Config`, `Benchmark History`, or `Benchmark Archive`.

## Getting more diagnostic information

Keep the complete terminal traceback and the messages immediately before it.
Also record:

- the operating system and Python version;
- the tracker version or Git commit;
- which worksheet was being updated;
- whether the spreadsheet was blank or already contained data;
- whether another tracker instance was running.

Never include the service account JSON content in a bug report.
