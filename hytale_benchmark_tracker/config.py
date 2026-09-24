from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

if __name__ == "__main__" and not __package__:
    print("config.py is an internal module and cannot start the application.")
    print("Run this command from the project root: python log_to_sheets.py")
    raise SystemExit(2)

from .constants import (
    DEFAULT_LOG_PROFILE,
    LOG_PROFILE_PATHS,
    SUPPORTED_LOG_PROFILES,
)
from .google_api import ensure_worksheet, google_api_call


MIN_CONFIG_REFRESH_SECONDS = 8.0


class ConfigError(ValueError):
    """Raised when the local JSON configuration cannot be used safely."""


def load_config(path: Path) -> dict:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ConfigError(
            f"Configuration file not found: {path}\n"
            "Copy config.example.json to config.json, then fill in the local values."
        )

    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(cfg, dict):
        raise ConfigError("config.json must contain one JSON object.")

    required = ["service_account_json", "spreadsheet_id"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ConfigError(f"Missing required config.json values: {', '.join(missing)}")

    cfg.setdefault("sample_count", 1000)
    cfg.setdefault("sample_counts", [cfg["sample_count"]])
    cfg.setdefault("log_files", [cfg.get("log_file", "")])
    cfg.setdefault("log_profile", DEFAULT_LOG_PROFILE)
    # Backward compatibility: an existing local log_folder remains a local
    # override. It is never exposed to or overwritten by Google Sheets.
    cfg.setdefault("log_folder_override", cfg.get("log_folder", ""))
    cfg.setdefault("world_structures", [])
    cfg.setdefault("collection_enabled", True)
    cfg.setdefault("test_mode", False)
    cfg.setdefault("resume_from", "")
    cfg.setdefault("poll_seconds", 1.0)
    cfg.setdefault("config_refresh_seconds", 30)
    cfg.setdefault("start_from_end", False)
    # Empty sheet names are invalid even when the JSON key exists. Normalize
    # them here instead of relying on setdefault(), which only handles missing
    # keys.
    sheet_defaults = {
        "config_sheet": "Config",
        "history_sheet": "Benchmark History",
        "archive_sheet": "Benchmark Archive",
        "comparison_sheet": "Analyse moderne",
    }
    for key, default in sheet_defaults.items():
        if not str(cfg.get(key, "")).strip():
            cfg[key] = default
    cfg.setdefault("analysis_recent_days", 30)
    cfg.setdefault("state_file", ".log_watcher_state.json")
    try:
        return validate_local_config(cache_runtime_filters(cfg), path)
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def cache_runtime_filters(cfg: dict) -> dict:
    """Cache normalized filter sets used for every parsed benchmark."""
    cfg["_sample_count_set"] = frozenset(
        int(value)
        for value in cfg.get("sample_counts", [cfg.get("sample_count", 1000)])
        if str(value).strip()
    )
    cfg["_world_structure_set"] = frozenset(
        str(value).strip()
        for value in cfg.get("world_structures", [])
        if str(value).strip()
    )
    cfg["_sample_counts_label"] = ", ".join(
        str(value)
        for value in cfg.get("sample_counts", [cfg.get("sample_count", 1000)])
        if str(value).strip()
    ) or "all"
    cfg["_resume_datetime"] = parse_resume_datetime(cfg.get("resume_from", ""))
    return cfg


def split_config_values(value: object) -> List[str]:
    """Accept comma-, semicolon-, or newline-separated values."""
    return [part.strip() for part in re.split(r"[,;\r\n]+", str(value)) if part.strip()]


def validate_log_file_patterns(items: List[object]) -> List[str]:
    """Allow file names and flat patterns, never paths from Google Sheets."""
    validated = []
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        candidate = Path(name)
        if (
            candidate.is_absolute()
            or candidate.anchor
            or "/" in name
            or "\\" in name
            or "**" in name
            or name in {".", ".."}
        ):
            raise ValueError(
                f"Unsafe log_files entry {name!r}: use a file name or flat pattern only"
            )
        if not name.casefold().endswith(".log"):
            raise ValueError(
                f"Invalid log_files entry {name!r}: only .log files are allowed"
            )
        validated.append(name)
    return validated


def validate_log_profile(value: object) -> str:
    """Return a known local log profile, never an arbitrary path."""
    profile = str(value or DEFAULT_LOG_PROFILE).strip().casefold()
    if profile not in SUPPORTED_LOG_PROFILES:
        choices = ", ".join(sorted(SUPPORTED_LOG_PROFILES))
        raise ValueError(f"Unknown log_profile {profile!r}. Allowed values: {choices}")
    return profile


def resolve_log_folder(cfg: dict) -> Path:
    """Resolve the local-only override or the selected safe log profile."""
    override = str(cfg.get("log_folder_override", "")).strip()
    if override:
        folder = Path(override).expanduser()
        source = "local override"
    else:
        profile = validate_log_profile(cfg.get("log_profile", DEFAULT_LOG_PROFILE))
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            raise ValueError(
                "APPDATA is unavailable; set log_folder_override in config.json"
            )
        folder = Path(appdata).joinpath(*LOG_PROFILE_PATHS[profile])
        source = f"profile {profile}"

    try:
        resolved = folder.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"Log folder not found ({source}): {folder}") from exc
    if not resolved.is_dir():
        raise ValueError(f"Configured log folder is not a directory: {resolved}")
    return resolved


def _resolve_config_path(value: object, config_dir: Path) -> Path:
    """Resolve a user-local path relative to the directory containing config.json."""
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    return candidate.resolve(strict=False)


def validate_local_config(cfg: dict, config_path: Path) -> dict:
    """Validate and normalize machine-specific settings before any API call."""
    config_dir = config_path.parent

    spreadsheet_id = str(cfg.get("spreadsheet_id", "")).strip()
    url_match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", spreadsheet_id)
    if url_match:
        spreadsheet_id = url_match.group(1)
    if not spreadsheet_id or spreadsheet_id == "your-google-spreadsheet-id":
        raise ConfigError("Set spreadsheet_id in config.json.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_id):
        raise ConfigError("spreadsheet_id must be a Google Sheet ID or its full URL.")
    cfg["spreadsheet_id"] = spreadsheet_id

    service_account = str(cfg.get("service_account_json", "")).strip()
    if not service_account or service_account == "path/to/service-account.json":
        raise ConfigError("Set service_account_json in config.json.")
    service_account_path = _resolve_config_path(service_account, config_dir)
    if not service_account_path.is_file():
        raise ConfigError(f"Google service account file not found: {service_account_path}")
    cfg["service_account_json"] = str(service_account_path)

    override = str(cfg.get("log_folder_override", "")).strip()
    if override:
        cfg["log_folder_override"] = str(_resolve_config_path(override, config_dir))
    cfg["log_profile"] = validate_log_profile(cfg.get("log_profile", DEFAULT_LOG_PROFILE))
    resolve_log_folder(cfg)

    log_files = cfg.get("log_files", [])
    if not isinstance(log_files, list):
        raise ConfigError("log_files must be a JSON list of file names or flat patterns.")
    cfg["log_files"] = validate_log_file_patterns(log_files)
    if not cfg["log_files"]:
        raise ConfigError("log_files must contain at least one .log file name or pattern.")

    sample_counts = cfg.get("sample_counts", [])
    if not isinstance(sample_counts, list) or not sample_counts:
        raise ConfigError("sample_counts must be a non-empty JSON list.")
    cfg["sample_counts"] = [int(value) for value in sample_counts]
    if any(value <= 0 for value in cfg["sample_counts"]):
        raise ConfigError("sample_counts values must be positive integers.")

    world_structures = cfg.get("world_structures", [])
    if not isinstance(world_structures, list):
        raise ConfigError("world_structures must be a JSON list.")
    cfg["world_structures"] = [str(value).strip() for value in world_structures if str(value).strip()]

    cfg["poll_seconds"] = float(cfg["poll_seconds"])
    if cfg["poll_seconds"] < 0.2:
        raise ConfigError("poll_seconds must be at least 0.2.")
    cfg["config_refresh_seconds"] = float(cfg["config_refresh_seconds"])
    if cfg["config_refresh_seconds"] < MIN_CONFIG_REFRESH_SECONDS:
        raise ConfigError(
            f"config_refresh_seconds must be at least {MIN_CONFIG_REFRESH_SECONDS:g}."
        )
    cfg["analysis_recent_days"] = int(cfg["analysis_recent_days"])
    if cfg["analysis_recent_days"] < 0:
        raise ConfigError("analysis_recent_days cannot be negative.")

    state_path = _resolve_config_path(cfg["state_file"], config_dir)
    cfg["state_file"] = str(state_path)
    return cache_runtime_filters(cfg)


def config_bool(value: object, default=True) -> bool:
    if isinstance(value, bool):
        return value
    # A leading apostrophe is how Sheets preserves a boolean-looking value as
    # text. Remove it before migrating legacy TRUE/FALSE or VRAI/FAUX cells.
    text = str(value).strip().lstrip("'").strip().lower()
    if text in {"true", "vrai", "1", "yes", "oui"}:
        return True
    if text in {"false", "faux", "0", "no", "non"}:
        return False
    return default


def parse_resume_datetime(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for parser in (
        datetime.fromisoformat,
        lambda item: datetime.strptime(item, "%d/%m/%Y %H:%M:%S"),
        lambda item: datetime.strptime(item, "%d/%m/%Y %H:%M"),
    ):
        try:
            return parser(text)
        except ValueError:
            continue
    raise ValueError(
        "Invalid resume date. Use YYYY-MM-DD HH:MM:SS "
        "or DD/MM/YYYY HH:MM."
    )


def parse_filter_datetime(value: object, end_of_day=False) -> Optional[datetime]:
    """Parse an analysis time-filter boundary with optional date-only input."""
    text = str(value or "").strip()
    if not text:
        return None
    date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}", text))
    formats = (
        None,
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    )
    for date_format in formats:
        try:
            parsed = (
                datetime.fromisoformat(text)
                if date_format is None
                else datetime.strptime(text, date_format)
            )
            if date_only and end_of_day:
                return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed
        except ValueError:
            continue
    raise ValueError(
        "Invalid analysis date. Use YYYY-MM-DD HH:MM:SS or DD/MM/YYYY HH:MM."
    )


def config_default_rows(cfg: dict) -> List[List[object]]:
    log_files = cfg.get("log_files") or [cfg.get("log_file", "")]
    sample_counts = cfg.get("sample_counts") or [cfg.get("sample_count", 1000)]
    return [
        ["Parameter", "Value", "Description"],
        ["collection_enabled", bool(cfg.get("collection_enabled", True)),
         "Checked = data collection is enabled"],
        ["test_mode", bool(cfg.get("test_mode", False)),
         "Checked = new benchmarks are marked as tests"],
        ["resume_from", cfg.get("resume_from", ""),
         "Ignore benchmarks older than this date and time"],
        ["log_profile", validate_log_profile(cfg.get("log_profile", DEFAULT_LOG_PROFILE)),
         "Safe local folder profile; paths stay in each user's local config"],
        ["log_files", ", ".join(str(item) for item in log_files if item),
         "File names or local patterns (* and ?), without folders"],
        ["sample_counts", ", ".join(str(item) for item in sample_counts),
         "Accepted Sample Count values, separated by commas"],
        ["world_structures", ", ".join(cfg.get("world_structures", [])),
         "Leave blank to accept every World Structure"],
        ["poll_seconds", cfg.get("poll_seconds", 1.0), "Log polling interval in seconds"],
        ["analysis_recent_days", cfg.get("analysis_recent_days", 30),
         "Default Recent days value for new analysis sheets"],
        ["config_refresh_seconds", cfg.get("config_refresh_seconds", 30),
         f"Interval for reloading this sheet, in seconds (minimum {MIN_CONFIG_REFRESH_SECONDS:g})"],
        ["basic_reference_status", "Checking...",
         "Read-only status for the static Basic reference used in analysis sheets"],
    ]


def ensure_config_sheet(book, cfg: dict):
    """Create the functional configuration sheet without exposing secrets."""
    ws = ensure_worksheet(book, cfg.get("config_sheet", "Config"), rows=30, cols=3)
    values = google_api_call(ws.get, range_name="A1:C30")
    defaults = config_default_rows(cfg)
    obsolete_rows = [
        index
        for index, row in enumerate(values, start=1)
        if row and str(row[0]).strip() in {
            "analysis_from", "analysis_to", "log_folder"
        }
    ]
    if obsolete_rows:
        google_api_call(ws.spreadsheet.batch_update, {"requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": row_index - 1,
                        "endIndex": row_index,
                    }
                }
            }
            for row_index in reversed(obsolete_rows)
        ]})
        values = [
            row for index, row in enumerate(values, start=1)
            if index not in obsolete_rows
        ]

    if not values:
        google_api_call(
            ws.update,
            values=defaults,
            range_name=f"A1:C{len(defaults)}",
            value_input_option="RAW",
        )
        values = defaults
    else:
        existing_rows = {
            str(row[0]).strip(): row_index
            for row_index, row in enumerate(values[1:], start=2)
            if row and str(row[0]).strip()
        }
        missing = [row for row in defaults[1:] if str(row[0]) not in existing_rows]
        text_updates = [{"range": "A1:C1", "values": [defaults[0]]}]
        for default_row in defaults[1:]:
            row_index = existing_rows.get(str(default_row[0]))
            if row_index:
                text_updates.append({
                    "range": f"C{row_index}",
                    "values": [[default_row[2]]],
                })
        if missing:
            start_row = len(values) + 1
            text_updates.append({
                "range": f"A{start_row}:C{start_row + len(missing) - 1}",
                "values": missing,
            })
            values += missing
        google_api_call(ws.batch_update, text_updates, value_input_option="RAW")

    config_rows = {
        str(row[0]).strip(): index
        for index, row in enumerate(values, start=1)
        if row and str(row[0]).strip()
    }
    cfg["_basic_reference_status_row"] = config_rows.get(
        "basic_reference_status", len(defaults)
    )
    enabled_row = config_rows.get("collection_enabled", 2)
    test_mode_row = config_rows.get("test_mode", 3)
    log_profile_row = config_rows.get("log_profile")
    requests = [
        {
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": enabled_row - 1,
                    "endRowIndex": enabled_row,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": test_mode_row - 1,
                    "endRowIndex": test_mode_row,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.07, "green": 0.09, "blue": 0.15},
                        "horizontalAlignment": "CENTER",
                        "textFormat": {
                            "bold": True,
                            "fontFamily": "Arial",
                            "fontSize": 9,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": len(values),
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.88, "green": 0.93, "blue": 0.98},
                        "horizontalAlignment": "LEFT",
                        "textFormat": {
                            "bold": True,
                            "fontFamily": "Arial",
                            "fontSize": 8,
                            "foregroundColor": {"red": 0.10, "green": 0.22, "blue": 0.38},
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": len(values),
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1.00, "green": 0.98, "blue": 0.88},
                        "horizontalAlignment": "LEFT",
                    }
                },
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": len(values),
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.97, "green": 0.98, "blue": 0.99},
                        "horizontalAlignment": "LEFT",
                        "textFormat": {"italic": True},
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.horizontalAlignment,"
                    "userEnteredFormat.textFormat.italic"
                ),
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": len(values),
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "borders": {
                            "top": {"style": "SOLID", "color": {"red": 0.78, "green": 0.81, "blue": 0.86}},
                            "bottom": {"style": "SOLID", "color": {"red": 0.78, "green": 0.81, "blue": 0.86}},
                            "left": {"style": "SOLID", "color": {"red": 0.78, "green": 0.81, "blue": 0.86}},
                            "right": {"style": "SOLID", "color": {"red": 0.78, "green": 0.81, "blue": 0.86}},
                        }
                    }
                },
                "fields": "userEnteredFormat.borders",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": enabled_row - 1,
                    "endRowIndex": enabled_row,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": len(values),
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"fontFamily": "Arial", "fontSize": 8},
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                    }
                },
                "fields": (
                    "userEnteredFormat.textFormat.fontFamily,"
                    "userEnteredFormat.textFormat.fontSize,"
                    "userEnteredFormat.verticalAlignment,"
                    "userEnteredFormat.wrapStrategy"
                ),
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 160},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                "properties": {"pixelSize": 360},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
                "properties": {"pixelSize": 360},
                "fields": "pixelSize",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {"frozenRowCount": 1, "hideGridlines": False},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines",
            }
        },
    ]
    if log_profile_row:
        requests.insert(2, {
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": log_profile_row - 1,
                    "endRowIndex": log_profile_row,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": name}
                            for name in sorted(SUPPORTED_LOG_PROFILES)
                        ],
                    },
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        })
    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})
    return ws


def update_basic_reference_status(ws, cfg: dict, reference: Optional[dict]):
    """Update the Config dashboard only when the Basic status changes."""
    status = (
        f"Ready - latest active Basic: {reference['timestamp']}"
        if reference else
        "Missing - run a benchmark with WorldStructure Name Basic"
    )
    if cfg.get("_basic_reference_status_value") == status:
        return
    row = int(cfg.get(
        "_basic_reference_status_row", len(config_default_rows(cfg))
    ))
    color = (
        {"red": 0.86, "green": 0.96, "blue": 0.89}
        if reference else
        {"red": 1.0, "green": 0.91, "blue": 0.80}
    )
    google_api_call(ws.spreadsheet.batch_update, {"requests": [{
        "updateCells": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": row - 1,
                "endRowIndex": row,
                "startColumnIndex": 1,
                "endColumnIndex": 2,
            },
            "rows": [{"values": [{
                "userEnteredValue": {"stringValue": status},
                "userEnteredFormat": {
                    "backgroundColor": color,
                    "horizontalAlignment": "LEFT",
                    "textFormat": {
                        "bold": True,
                        "fontFamily": "Arial",
                        "fontSize": 8,
                    },
                },
            }]}],
            "fields": "userEnteredValue,userEnteredFormat",
        }
    }]})
    cfg["_basic_reference_status_value"] = status


def read_sheet_config(ws, base_cfg: dict) -> dict:
    """Merge safe sheet settings with connection secrets from local JSON."""
    rows = google_api_call(ws.get, range_name="A2:B30")
    settings = {
        str(row[0]).strip(): row[1] if len(row) > 1 else ""
        for row in rows
        if row and str(row[0]).strip()
    }
    cfg = dict(base_cfg)
    cfg["collection_enabled"] = config_bool(settings.get("collection_enabled", True))
    cfg["test_mode"] = config_bool(settings.get("test_mode", False), False)
    cfg["resume_from"] = str(settings.get("resume_from", "")).strip()
    parse_resume_datetime(cfg["resume_from"])
    cfg["log_profile"] = validate_log_profile(
        settings.get("log_profile", cfg.get("log_profile", DEFAULT_LOG_PROFILE))
    )
    cfg["log_files"] = validate_log_file_patterns(split_config_values(
        settings.get("log_files", ", ".join(cfg.get("log_files", [])))
    ))
    cfg["sample_counts"] = [
        int(value) for value in split_config_values(
            settings.get("sample_counts", ", ".join(map(str, cfg.get("sample_counts", []))))
        )
    ]
    cfg["world_structures"] = split_config_values(settings.get("world_structures", ""))
    cfg["poll_seconds"] = max(0.2, float(settings.get("poll_seconds", cfg["poll_seconds"])))
    cfg["analysis_recent_days"] = max(
        0, int(float(settings.get("analysis_recent_days", cfg["analysis_recent_days"])))
    )
    cfg["config_refresh_seconds"] = max(
        MIN_CONFIG_REFRESH_SECONDS,
        float(settings.get("config_refresh_seconds", cfg["config_refresh_seconds"])),
    )
    return cache_runtime_filters(cfg)
