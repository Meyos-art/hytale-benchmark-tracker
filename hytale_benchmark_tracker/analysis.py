from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import gspread

from .constants import ANALYSIS_ROW_STYLES, ANALYSIS_SECTION_COLORS, STAGE_TOKEN_RE
from .config import parse_filter_datetime
from .google_api import _extended_value, ensure_worksheet, google_api_call
from .history import remove_legacy_unit_column


def canonical_analysis_key(key: str) -> str:
    """Remove shifting Stage-N suffixes from stored analysis row keys."""
    return STAGE_TOKEN_RE.sub("", str(key))


def canonical_analysis_label(label: str) -> str:
    """Remove shifting Stage-N suffixes while preserving visual indentation."""
    return STAGE_TOKEN_RE.sub("", str(label))


def normalize_analysis_stage_rows(ws, headers: List[str]) -> bool:
    """Merge legacy analysis rows that differ only by their Stage-N suffix."""
    if len(headers) < 2:
        return False
    last_column = gspread.utils.rowcol_to_a1(1, len(headers)).rstrip("1")
    rows = google_api_call(ws.get, range_name=f"A5:{last_column}")
    if not rows:
        return False

    merged_rows: List[List[object]] = []
    row_by_key: Dict[str, List[object]] = {}
    changed = False
    for source in rows:
        padded = source + [""] * (len(headers) - len(source))
        raw_key = str(padded[0])
        if not raw_key:
            continue
        key = canonical_analysis_key(raw_key)
        label = canonical_analysis_label(str(padded[1]))
        if key != raw_key or label != str(padded[1]):
            changed = True
        if key not in row_by_key:
            target = padded[:len(headers)]
            target[0] = key
            target[1] = label
            row_by_key[key] = target
            merged_rows.append(target)
            continue

        changed = True
        target = row_by_key[key]
        for column in range(2, len(headers)):
            if target[column] == "" and padded[column] != "":
                target[column] = padded[column]

    if not changed:
        return False

    end_row = 4 + len(merged_rows)
    google_api_call(
        ws.update,
        values=merged_rows,
        range_name=f"A5:{last_column}{end_row}",
        value_input_option="RAW",
    )
    old_end_row = 4 + len(rows)
    if end_row < old_end_row:
        google_api_call(
            ws.batch_clear,
            [f"A{end_row + 1}:{last_column}{old_end_row}"],
        )
    print(
        f"[MIGRATE] {ws.title}: removed Stage-N suffixes and merged "
        f"{len(rows) - len(merged_rows)} duplicate metric rows."
    )
    return True


def world_sheet_title(world: str, history_title: str) -> str:
    """Create a valid Google Sheets title from a World Structure Name."""
    title = re.sub(r"[\\/?*\[\]:]", "_", world).strip(" '") or "World Structure"
    if title.casefold() == history_title.casefold():
        title = f"{title} - Analyse"
    return title[:100]


def ensure_analysis_filter_row(
    ws,
    prefetched_rows: Optional[List[List[object]]] = None,
) -> List[str]:
    """Ensure title, description, archive, and hidden hash metadata rows."""
    if prefetched_rows is None:
        last_column = gspread.utils.rowcol_to_a1(
            1, max(2, ws.col_count)
        ).rstrip("1")
        prefetched_rows = google_api_call(
            ws.get,
            range_name=f"A1:{last_column}4",
        )
        first_row = prefetched_rows[0] if len(prefetched_rows) > 0 else []
        second_row = prefetched_rows[1] if len(prefetched_rows) > 1 else []
        third_row = prefetched_rows[2] if len(prefetched_rows) > 2 else []
        fourth_row = prefetched_rows[3] if len(prefetched_rows) > 3 else []
    else:
        first_row = prefetched_rows[0] if len(prefetched_rows) > 0 else []
        second_row = prefetched_rows[1] if len(prefetched_rows) > 1 else []
        third_row = prefetched_rows[2] if len(prefetched_rows) > 2 else []
        fourth_row = prefetched_rows[3] if len(prefetched_rows) > 3 else []
    current_layout = (
        len(first_row) >= 2
        and first_row[0] == "Key"
        and first_row[1] == "Metrics"
        and second_row
        and second_row[0] == "meta:description"
        and third_row
        and third_row[0] == "meta:archive"
        and fourth_row
        and fourth_row[0] == "meta:hash"
    )
    if current_layout:
        return first_row
    requests = []
    if second_row and second_row[0] == "Key":
        headers = second_row
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                }
            }
        })
        requests.append({
            "insertDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": 4,
                },
                "inheritFromBefore": False,
            }
        })
    elif first_row and first_row[0] == "Key":
        headers = first_row
        if not (second_row and second_row[0] == "meta:description"):
            requests.append({
                "insertDimension": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": 1,
                    "endIndex": 4,
                    },
                    "inheritFromBefore": False,
                }
            })
    else:
        headers = []

    # Migrate the previous five-row metadata layout by removing the visible
    # Test Mode row. Test status remains in history and in the title color.
    if fourth_row and fourth_row[0] == "meta:test":
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 3,
                    "endIndex": 4,
                }
            }
        })

    requests.extend([
        {
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 4,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "rows": [
                    {"values": [{"userEnteredValue": {"stringValue": "Key"}}, {"userEnteredValue": {"stringValue": "Metrics"}}]},
                    {"values": [{"userEnteredValue": {"stringValue": "meta:description"}}, {"userEnteredValue": {"stringValue": "Description"}}]},
                    {"values": [{"userEnteredValue": {"stringValue": "meta:archive"}}, {"userEnteredValue": {"stringValue": "Archive"}}]},
                    {"values": [{"userEnteredValue": {"stringValue": "meta:hash"}}, {"userEnteredValue": {"stringValue": "Block Hash"}}]},
                ],
                "fields": "userEnteredValue",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": 3, "endIndex": 4},
                "properties": {"pixelSize": 2, "hiddenByUser": True},
                "fields": "pixelSize,hiddenByUser",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {
                        "frozenRowCount": 0,
                        "frozenColumnCount": 0,
                    },
                },
                "fields": (
                    "gridProperties.frozenRowCount,"
                    "gridProperties.frozenColumnCount"
                ),
            }
        },
    ])
    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})
    return headers


def analysis_empty_grid_cleanup_requests(
    ws,
    used_column_count: int,
    used_row_count: int,
) -> List[dict]:
    """Keep unused analysis cells blank until a benchmark or metric exists."""
    requests = []
    white = {"red": 1.0, "green": 1.0, "blue": 1.0}
    separator = {
        "style": "SOLID_MEDIUM",
        "color": {"red": 0.62, "green": 0.65, "blue": 0.70},
    }
    used_column_count = max(2, min(used_column_count, ws.col_count))
    used_row_count = max(4, min(used_row_count, ws.row_count))

    requests.extend([
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {
                        "frozenRowCount": 0,
                        "frozenColumnCount": 0,
                    },
                },
                "fields": (
                    "gridProperties.frozenRowCount,"
                    "gridProperties.frozenColumnCount"
                ),
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": used_row_count,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "borders": {"right": separator}
                    }
                },
                "fields": "userEnteredFormat.borders.right",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 1,
                    "endColumnIndex": used_column_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "borders": {"bottom": separator}
                    }
                },
                "fields": "userEnteredFormat.borders.bottom",
            }
        },
    ])

    if used_column_count < ws.col_count:
        # Clear legacy FALSE values and checkbox validation after the final
        # benchmark column. New columns receive both only when they are added.
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": used_column_count,
                    "endColumnIndex": ws.col_count,
                },
                "fields": "userEnteredValue,dataValidation",
            }
        })
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 4,
                    "startColumnIndex": used_column_count,
                    "endColumnIndex": ws.col_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": white,
                        "borders": {},
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.borders"
                ),
            }
        })

    if used_row_count < ws.row_count:
        # Stop the Metrics-column styling at the final populated metric row.
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": used_row_count,
                    "endRowIndex": ws.row_count,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": white,
                        "borders": {},
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.borders"
                ),
            }
        })
    return requests


def default_analysis_controls() -> dict:
    """Return the active analysis controls after removing the legacy filter bar."""
    return {"from": "", "to": "", "archive_before": "", "archive": False}


def get_world_analysis_state(book, world: str, history_title: str, cache: dict, cfg: dict):
    """Open or create and cache the analysis sheet for one world."""
    if world in cache:
        return cache[world]

    title = world_sheet_title(world, history_title)
    if title in cache:
        cache[world] = cache[title]
        return cache[world]
    ws = ensure_worksheet(book, title)
    headers = ensure_analysis_filter_row(ws)
    headers = remove_legacy_unit_column(ws, headers)
    normalize_analysis_stage_rows(ws, headers)
    controls = default_analysis_controls()
    apply_analysis_time_filter(ws, headers, controls, cfg)
    metric_rows = google_api_call(ws.get, range_name="A5:A")
    metric_keys = [row[0] for row in metric_rows if row]
    cleanup_requests = analysis_empty_grid_cleanup_requests(
        ws,
        len(headers),
        4 + len(metric_rows),
    )
    if cleanup_requests:
        google_api_call(ws.spreadsheet.batch_update, {"requests": cleanup_requests})
    keys = ["Key"] + metric_keys if metric_keys else []
    state = {
        "headers": headers,
        "keys": keys,
        "hashes": set(google_api_call(ws.row_values, 4)[2:]),
        "controls": controls,
        # Existing sheets retain their row groups and the user's chosen state.
        # The script no longer rebuilds them at every startup.
        "groups_updated": bool(headers and keys),
    }
    cache[world] = (ws, state)
    return ws, state


def add_detail_groups(ws, display_rows: List[Dict[str, object]]):
    """Build useful row groups for categories and subcategories."""
    requests = []

    # Delete old groups from deepest to shallowest.
    # Rebuilding still uses a single batched API request.
    existing_groups = sorted(
        ws.list_dimension_group_rows(),
        key=lambda group: int(group.get("depth", 0)),
        reverse=True,
    )
    for group in existing_groups:
        group_range = group.get("range", {})
        requests.append({
            "deleteDimensionGroup": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": int(group_range.get("startIndex", 0)),
                    "endIndex": int(group_range.get("endIndex", 0)),
                }
            }
        })

    requests.append(
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }
    )
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": ws.id,
                "dimension": "ROWS",
            "startIndex": 4,
            "endIndex": len(display_rows) + 4,
            },
            "properties": {"hiddenByUser": False},
            "fields": "hiddenByUser",
        }
    })

    # Every major category remains visible and controls all rows up to the
    # spacer before the next category.
    for index, row in enumerate(display_rows):
        if row["kind"] != "section":
            continue

        next_boundary = index + 1
        while (
            next_boundary < len(display_rows)
            and display_rows[next_boundary]["kind"] not in {"section", "spacer"}
        ):
            next_boundary += 1

        if next_boundary > index + 1:
            start_index = index + 5
            end_index = next_boundary + 4
            requests.append({
                "addDimensionGroup": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": start_index,
                        "endIndex": end_index,
                    }
                }
            })
            section_name = str(row["label"]).strip()
            if section_name in {"Data Transfer", "Memory Usage Report"}:
                requests.append({
                    "updateDimensionGroup": {
                        "dimensionGroup": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "ROWS",
                                "startIndex": start_index,
                                "endIndex": end_index,
                            },
                            "depth": 1,
                            "collapsed": True,
                        },
                        "fields": "collapsed",
                    }
                })

    # Stages and grids keep their own group except in these two reports, whose
    # short contents are clearer directly below the major category.
    no_subcategory_groups = {"Memory Usage Report", "Context Dependency Report"}
    boundaries = {"subcategory", "section", "spacer"}
    current_section = None
    for index, row in enumerate(display_rows):
        if row["kind"] == "section":
            current_section = str(row["label"]).strip()
            continue
        if row["kind"] != "subcategory" or current_section in no_subcategory_groups:
            continue

        subcategory_row = index + 5
        next_boundary = index + 1
        while next_boundary < len(display_rows) and display_rows[next_boundary]["kind"] not in boundaries:
            next_boundary += 1

        last_detail_row = next_boundary + 4
        if last_detail_row > subcategory_row:
            requests.append({
                "addDimensionGroup": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": subcategory_row,
                        "endIndex": last_detail_row,
                    }
                }
            })
            if current_section == "Content Generation":
                requests.append({
                    "updateDimensionGroup": {
                        "dimensionGroup": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "ROWS",
                                "startIndex": subcategory_row,
                                "endIndex": last_detail_row,
                            },
                            "depth": 2,
                            "collapsed": True,
                        },
                        "fields": "collapsed",
                    }
                })

    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def analysis_header(value: object) -> str:
    """Display analysis timestamps at second precision."""
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+", text):
        return text.split(".", 1)[0]
    return text


def analysis_visibility_requests(
    ws,
    headers: List[str],
    controls: dict,
    cfg: dict,
) -> List[dict]:
    """Build batched column visibility requests for the configured time window."""
    dated_headers = []
    for column_index, header in enumerate(headers[2:], start=2):
        try:
            dated_headers.append((column_index, datetime.fromisoformat(str(header))))
        except (TypeError, ValueError):
            continue
    if not dated_headers:
        return []

    lower = parse_filter_datetime(controls.get("from", ""))
    upper = parse_filter_datetime(controls.get("to", ""), end_of_day=True)
    if not lower and not upper:
        recent_days = int(controls.get("recent_days", cfg.get("analysis_recent_days", 30)))
        if recent_days > 0:
            lower = max(date for _, date in dated_headers) - timedelta(days=recent_days)

    statuses = [
        (column, bool((lower and date < lower) or (upper and date > upper)))
        for column, date in dated_headers
    ]
    requests = []
    run_start, run_hidden = statuses[0]
    previous_column = run_start
    for column, hidden in statuses[1:]:
        if hidden != run_hidden or column != previous_column + 1:
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": run_start,
                        "endIndex": previous_column + 1,
                    },
                    "properties": {"hiddenByUser": run_hidden},
                    "fields": "hiddenByUser",
                }
            })
            run_start, run_hidden = column, hidden
        previous_column = column
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": ws.id,
                "dimension": "COLUMNS",
                "startIndex": run_start,
                "endIndex": previous_column + 1,
            },
            "properties": {"hiddenByUser": run_hidden},
            "fields": "hiddenByUser",
        }
    })
    return requests


def apply_analysis_time_filter(
    ws,
    headers: List[str],
    controls: dict,
    cfg: dict,
    pending_requests: Optional[List[dict]] = None,
):
    """Normalize existing headers, use 6 pt titles, and apply the time filter."""
    normalized = headers[:2] + [analysis_header(value) for value in headers[2:]]
    requests = []
    if normalized != headers and len(normalized) > 2:
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 2,
                    "endColumnIndex": len(normalized),
                },
                "rows": [{
                    "values": [
                        {"userEnteredValue": {"stringValue": value}}
                        for value in normalized[2:]
                    ]
                }],
                "fields": "userEnteredValue",
            }
        })
        headers[:] = normalized
    if len(headers) > 2:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 2,
                    "endColumnIndex": len(headers),
                },
                "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 6}}},
                "fields": "userEnteredFormat.textFormat.fontSize",
            }
        })
    requests.extend(analysis_visibility_requests(ws, headers, controls, cfg))
    if pending_requests is not None:
        pending_requests.extend(requests)
    elif requests:
        google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def load_existing_analysis_sheets(book, cfg: dict, cache: dict):
    """Load and refresh every existing per-world analysis sheet once."""
    reserved = {
        str(cfg.get("history_sheet", "Benchmark History")).casefold(),
        str(cfg.get("archive_sheet", "Benchmark Archive")).casefold(),
        str(cfg.get("config_sheet", "Config")).casefold(),
        str(cfg.get("comparison_sheet", "Analyse moderne")).casefold(),
    }
    candidates = [
        ws for ws in google_api_call(book.worksheets)
        if ws.title.casefold() not in reserved
    ]
    metadata_by_sheet = {}
    startup_format_requests = []
    if candidates:
        ranges = []
        for ws in candidates:
            escaped_title = ws.title.replace("'", "''")
            last_column = gspread.utils.rowcol_to_a1(
                1, max(2, ws.col_count)
            ).rstrip("1")
            ranges.append(f"'{escaped_title}'!A1:{last_column}4")
        response = google_api_call(book.values_batch_get, ranges=ranges)
        value_ranges = response.get("valueRanges", [])
        for index, ws in enumerate(candidates):
            rows = (
                value_ranges[index].get("values", [])
                if index < len(value_ranges) else []
            )
            metadata_by_sheet[ws.id] = rows

    for ws in candidates:
        metadata = metadata_by_sheet.get(ws.id, [])
        first_row = metadata[0] if len(metadata) > 0 else []
        second_row = metadata[1] if len(metadata) > 1 else []
        if not (first_row and first_row[0] == "Key") and not (
            second_row and second_row[0] == "Key"
        ):
            continue
        headers = ensure_analysis_filter_row(ws, prefetched_rows=metadata)
        if len(headers) < 2 or headers[0] != "Key":
            continue
        key_rows = google_api_call(ws.get, range_name="A5:B")
        needs_stage_migration = any(
            (row and canonical_analysis_key(str(row[0])) != str(row[0]))
            or (
                len(row) > 1
                and canonical_analysis_label(str(row[1])) != str(row[1])
            )
            for row in key_rows
        )
        if needs_stage_migration:
            normalize_analysis_stage_rows(ws, headers)
            key_rows = google_api_call(ws.get, range_name="A5:A")
        controls = default_analysis_controls()
        apply_analysis_time_filter(
            ws,
            headers,
            controls,
            cfg,
            pending_requests=startup_format_requests,
        )
        startup_format_requests.extend(
            analysis_empty_grid_cleanup_requests(
                ws,
                len(headers),
                4 + len(key_rows),
            )
        )
        cache[ws.title] = (ws, {
            "headers": headers,
            "keys": ["Key"] + [row[0] for row in key_rows if row],
            "hashes": set(google_api_call(ws.row_values, 4)[2:]),
            "controls": controls,
            "groups_updated": True,
        })
    if startup_format_requests:
        google_api_call(
            book.batch_update,
            {"requests": startup_format_requests},
        )


def format_comparison_layout(
    ws,
    display_rows: List[Dict[str, object]],
    keys: List[str],
    last_col: int,
    column_values: List[List[object]],
    headers: List[str],
    controls: dict,
    cfg: dict,
    test_mode: bool,
    initialize_layout: bool = False,
    new_metric_keys: Optional[set] = None,
):
    """Write one benchmark and format only its new column or new metric rows."""
    row_by_key = {
        key: index for index, key in enumerate(keys[1:], start=5)
    }
    last_row = max(4, len(keys) + 3)
    is_test = bool(test_mode)
    new_metric_keys = new_metric_keys or set()
    default_start_column = 1 if initialize_layout else last_col - 1

    requests = [{
        "updateCells": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 0,
                "endRowIndex": len(column_values),
                "startColumnIndex": last_col - 1,
                "endColumnIndex": last_col,
            },
            "rows": [
                {"values": [{"userEnteredValue": _extended_value(row[0])}]}
                for row in column_values
            ],
            "fields": "userEnteredValue",
        }
    }]

    def repeat_format(
        start_row: int,
        end_row: int,
        cell_format: dict,
        start_column: Optional[int] = None,
        end_column: Optional[int] = None,
    ):
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row,
                    "startColumnIndex": (
                        default_start_column
                        if start_column is None else start_column
                    ),
                    "endColumnIndex": (
                        last_col if end_column is None else end_column
                    ),
                },
                "cell": {"userEnteredFormat": cell_format},
                "fields": "userEnteredFormat",
            }
        })

    base_format = {
        "textFormat": {
            "fontFamily": "Arial",
            "fontSize": 6,
            "foregroundColor": {"red": 0.11, "green": 0.14, "blue": 0.19},
        },
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "CLIP",
    }
    repeat_format(1, last_row, base_format)

    new_row_numbers = [
        row_by_key[key]
        for key in new_metric_keys
        if key in row_by_key
    ]
    if new_row_numbers and not initialize_layout:
        repeat_format(
            min(new_row_numbers) - 1,
            max(new_row_numbers),
            base_format,
            start_column=1,
        )
    header_format = {
        "backgroundColor": (
            {"red": 0.07, "green": 0.48, "blue": 0.28}
            if is_test else
            {"red": 0.07, "green": 0.09, "blue": 0.15}
        ),
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "bold": True,
            "fontFamily": "Arial",
            "fontSize": 6,
            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
        },
    }
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": last_col - 1,
                "endColumnIndex": last_col,
            },
            "cell": {"userEnteredFormat": header_format},
            "fields": "userEnteredFormat",
        }
    })
    if initialize_layout:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {"userEnteredFormat": {
                    **header_format,
                    "backgroundColor": {"red": 0.07, "green": 0.09, "blue": 0.15},
                }},
                "fields": "userEnteredFormat",
            }
        })
    repeat_format(1, 3, {
        "backgroundColor": {"red": 0.96, "green": 0.97, "blue": 0.99},
        "textFormat": {"fontFamily": "Arial", "fontSize": 6},
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "CLIP",
    })

    for row in display_rows:
        key = str(row["key"])
        style = ANALYSIS_ROW_STYLES.get(str(row["kind"]))
        if row["kind"] == "section":
            fill, text_color = ANALYSIS_SECTION_COLORS.get(key, (
                {"red": 0.91, "green": 0.94, "blue": 0.98},
                {"red": 0.17, "green": 0.24, "blue": 0.34},
            ))
            style = {
                "backgroundColor": fill,
                "textFormat": {"bold": True, "fontSize": 8, "foregroundColor": text_color},
                "borders": {
                    "top": {"style": "SOLID_MEDIUM", "color": text_color},
                    "bottom": {"style": "SOLID", "color": text_color},
                },
            }
        row_number = row_by_key.get(str(row["key"]))
        if style and row_number:
            repeat_format(
                row_number - 1,
                row_number,
                style,
                start_column=(
                    1
                    if initialize_layout or key in new_metric_keys
                    else last_col - 1
                ),
            )

    if initialize_layout or new_row_numbers:
        row_start = 0 if initialize_layout else min(new_row_numbers) - 1
        row_end = last_row if initialize_layout else max(new_row_numbers)
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": row_start,
                    "endIndex": row_end,
                },
                "properties": {"pixelSize": 14},
                "fields": "pixelSize",
            }
        })
    for row in display_rows:
        key = str(row["key"])
        if row["kind"] == "spacer" and (
            initialize_layout or key in new_metric_keys
        ):
            row_number = row_by_key.get(str(row["key"]))
            if row_number:
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "ROWS",
                            "startIndex": row_number - 1,
                            "endIndex": row_number,
                        },
                        "properties": {"pixelSize": 6},
                        "fields": "pixelSize",
                    }
                })
    requests.extend([
        {
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": last_col - 1,
                    "endColumnIndex": last_col,
                },
                "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True},
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": last_col - 1, "endIndex": last_col},
                "properties": {"pixelSize": 95},
                "fields": "pixelSize",
            }
        },
    ])
    if initialize_layout:
        requests.extend([
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": 3,
                        "endIndex": 4,
                    },
                    "properties": {"pixelSize": 2, "hiddenByUser": True},
                    "fields": "pixelSize,hiddenByUser",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 1,
                    },
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 1,
                        "endIndex": 2,
                    },
                    "properties": {"pixelSize": 220},
                    "fields": "pixelSize",
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": ws.id,
                        "gridProperties": {
                            "hideGridlines": True,
                            "frozenRowCount": 0,
                            "frozenColumnCount": 0,
                        },
                    },
                    "fields": (
                        "gridProperties.hideGridlines,"
                        "gridProperties.frozenRowCount,"
                        "gridProperties.frozenColumnCount"
                    ),
                },
            },
        ])
    if last_col >= 3 and last_row >= 2:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 4,
                    "endRowIndex": last_row,
                    "startColumnIndex": (
                        2 if initialize_layout else last_col - 1
                    ),
                    "endColumnIndex": last_col,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        })

    separator = {
        "style": "SOLID_MEDIUM",
        "color": {"red": 0.62, "green": 0.65, "blue": 0.70},
    }
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 2,
                "endRowIndex": 3,
                "startColumnIndex": last_col - 1,
                "endColumnIndex": last_col,
            },
            "cell": {
                "userEnteredFormat": {
                    "borders": {"bottom": separator}
                }
            },
            "fields": "userEnteredFormat.borders.bottom",
        }
    })
    if initialize_layout or new_row_numbers:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": last_row,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "borders": {"right": separator}
                    }
                },
                "fields": "userEnteredFormat.borders.right",
            }
        })

    requests.extend(analysis_visibility_requests(ws, headers, controls, cfg))

    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def add_comparison_column(
    ws,
    timestamp: str,
    display_rows: List[Dict[str, object]],
    state: dict,
    cfg: dict,
    benchmark_hash: str,
    test_mode: bool,
    description: str = "",
):
    """Add a benchmark in C, D, E... with hidden keys in A and labels in B."""
    row_by_key = {str(row["key"]): row for row in display_rows}
    keys = list(row_by_key)

    existing_keys = state["keys"]
    is_new_layout = not existing_keys

    # Initialize the readable structure once.
    if is_new_layout:
        rows = [
            ["Key", "Metrics"],
            ["meta:description", "Description"],
            ["meta:archive", "Archive"],
            ["meta:hash", "Block Hash"],
        ] + [
            [str(row["key"]), row["label"]] for row in display_rows
        ]
        google_api_call(
            ws.update,
            values=rows,
            range_name=f"A1:B{len(rows)}",
            value_input_option="RAW",
        )
        existing_keys = ["Key"] + keys
        state["keys"] = existing_keys
        # These columns now physically exist in the sheet. Keeping them in the
        # cache prevents the next benchmark from overwriting column C.
        state["headers"] = ["Key", "Metrics"]

    # Append newly discovered metrics without overwriting history.
    existing_set = set(existing_keys[1:])
    missing = [key for key in keys if key not in existing_set]
    if missing:
        start = len(existing_keys) + 4
        google_api_call(
            ws.update,
            values=[[key, row_by_key[key]["label"]] for key in missing],
            range_name=f"A{start}:B{start + len(missing) - 1}",
            value_input_option="RAW",
        )
        existing_keys += missing

    # Cache headers to avoid rereading them for every benchmark.
    col = max(3, len(state["headers"]) + 1)
    header = analysis_header(timestamp)

    output = [
        [header],
        [description],
        [False],
        [benchmark_hash],
    ]
    for key in existing_keys[1:]:
        row = row_by_key.get(key)
        value = row["value"] if row else ""
        output.append([value])

    new_headers = state["headers"] + [header]
    format_comparison_layout(
        ws,
        display_rows,
        existing_keys,
        col,
        output,
        new_headers,
        state.get("controls", {}),
        cfg,
        test_mode,
        initialize_layout=is_new_layout,
        new_metric_keys=set(missing),
    )
    state["headers"].append(header)
    state.setdefault("hashes", set()).add(benchmark_hash)
