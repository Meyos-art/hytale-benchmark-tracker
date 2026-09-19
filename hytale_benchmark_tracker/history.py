from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import gspread

from .config import config_bool
from .google_api import _extended_value, google_api_call
from .parser import canonical_history_metric_key, ordered_history_headers


def normalize_history_schema(
    ws,
    headers: List[str],
    additional_headers: Optional[List[str]] = None,
) -> List[str]:
    """Merge and reorder Benchmark History columns without losing row data."""
    if not headers and not additional_headers:
        return headers

    normalized_headers = ordered_history_headers(
        headers + list(additional_headers or [])
    )
    target_by_header = {
        header: index for index, header in enumerate(normalized_headers)
    }
    source_to_target = [
        target_by_header[canonical_history_metric_key(header)]
        for header in headers
    ]

    if normalized_headers == headers:
        return headers

    old_last_cell = gspread.utils.rowcol_to_a1(1, len(headers)).rstrip("1")
    # Read native values before rebuilding the schema. Formatted values turn
    # numbers and booleans into strings (for example 1000 and FALSE), which
    # breaks checkbox rendering after dynamic metric columns are discovered.
    values = google_api_call(
        ws.get,
        range_name=f"A1:{old_last_cell}",
        value_render_option="UNFORMATTED_VALUE",
    )
    if not values:
        values = [headers]

    migrated = [normalized_headers]
    for source_row in values[1:]:
        padded = source_row + [""] * (len(headers) - len(source_row))
        target_row = [""] * len(normalized_headers)
        for source_column, value in enumerate(padded[:len(headers)]):
            target_column = source_to_target[source_column]
            if headers[source_column] == "In Analysis" and str(value).strip() != "":
                value = not config_bool(value, True)
            # Prefer an already-current Archive column over the legacy
            # inverted In Analysis value when both happen to exist.
            if value != "" and (
                target_row[target_column] == ""
                or headers[source_column] == "Archive"
            ):
                target_row[target_column] = value
        migrated.append(target_row)

    new_last_cell = gspread.utils.rowcol_to_a1(len(migrated), len(normalized_headers))
    google_api_call(
        ws.update,
        values=migrated,
        range_name=f"A1:{new_last_cell}",
        value_input_option="RAW",
    )
    if len(normalized_headers) < len(headers):
        trailing_start = gspread.utils.rowcol_to_a1(1, len(normalized_headers) + 1).rstrip("1")
        trailing_end = gspread.utils.rowcol_to_a1(1, len(headers)).rstrip("1")
        google_api_call(
            ws.batch_clear,
            [f"{trailing_start}1:{trailing_end}{max(2, len(migrated))}"],
        )
    print(
        f"[MIGRATE] Benchmark History reorganized: {len(headers)} -> "
        f"{len(normalized_headers)} columns."
    )
    return normalized_headers


def remove_legacy_unit_column(ws, headers: List[str]) -> List[str]:
    """Remove the legacy Unit column when it is still present."""
    if len(headers) >= 3 and headers[2].strip().lower() in {"unité", "unite"}:
        google_api_call(ws.spreadsheet.batch_update, {
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 2,
                        "endIndex": 3,
                    }
                }
            }]
        })
        return headers[:2] + headers[3:]
    return headers


def ensure_history_capacity(ws, state: dict, min_rows: int, min_columns: int):
    """Grow the history grid before writing a complete benchmark record."""
    current_rows = int(state.get("grid_rows", ws.row_count))
    current_columns = int(state.get("grid_columns", ws.col_count))
    target_rows = max(current_rows, min_rows)
    target_columns = max(current_columns, min_columns)
    if target_rows == current_rows and target_columns == current_columns:
        return

    google_api_call(ws.spreadsheet.batch_update, {"requests": [{
        "updateSheetProperties": {
            "properties": {
                "sheetId": ws.id,
                "gridProperties": {
                    "rowCount": target_rows,
                    "columnCount": target_columns,
                },
            },
            "fields": "gridProperties.rowCount,gridProperties.columnCount",
        }
    }]})
    state["grid_rows"] = target_rows
    state["grid_columns"] = target_columns


def upsert_history(ws, record: Dict[str, object], state: dict) -> str:
    """Write every benchmark field to its permanent history row."""
    headers = state["headers"]
    block_hash_value = str(record["Block Hash"])
    if block_hash_value in state["hash_rows"]:
        return "duplicate"

    fixed = ["Timestamp", "WorldStructure Name", "Sample Count", "Block Hash"]
    headers_changed = False
    if not headers:
        headers.extend(fixed)
        headers_changed = True

    # Insert newly discovered metrics into their logical report sections. This
    # keeps dynamic PropStage columns together instead of appending them after
    # Raw Benchmark.
    new_keys = [k for k in record.keys() if k not in headers]
    if new_keys:
        ensure_history_capacity(
            ws,
            state,
            min_rows=max(2, int(state.get("next_row", 2))),
            min_columns=max(4, len(headers) + len(new_keys)),
        )
        headers[:] = normalize_history_schema(ws, headers, new_keys)
        headers_changed = False

    ensure_history_capacity(
        ws,
        state,
        min_rows=max(2, int(state.get("next_row", 2))),
        min_columns=max(4, len(headers)),
    )
    if headers_changed:
        last_header_cell = gspread.utils.rowcol_to_a1(1, len(headers))
        google_api_call(
            ws.update,
            values=[headers],
            range_name=f"A1:{last_header_cell}",
            value_input_option="RAW",
        )

    target_row = int(state["next_row"])
    ensure_history_capacity(
        ws,
        state,
        min_rows=target_row,
        min_columns=len(headers),
    )
    row = [record.get(header, "") for header in headers]
    write_requests = [{
        "updateCells": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": target_row - 1,
                "endRowIndex": target_row,
                "startColumnIndex": 0,
                "endColumnIndex": len(headers),
            },
            "rows": [{
                "values": [
                    {"userEnteredValue": _extended_value(value)}
                    for value in row
                ]
            }],
            "fields": "userEnteredValue",
        }
    }]
    write_requests.extend(history_checkbox_requests(
        ws,
        headers,
        target_row,
        target_row,
    ))
    google_api_call(
        ws.spreadsheet.batch_update,
        {"requests": write_requests},
    )
    state["hash_rows"][block_hash_value] = target_row
    state["next_row"] = target_row + 1
    return "added"


def format_history_layout(ws):
    """Keep Benchmark History compact without deleting or truncating data."""
    row_count = max(2, ws.row_count)
    column_count = max(4, ws.col_count)
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": row_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"fontFamily": "Arial", "fontSize": 6},
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                    }
                },
                "fields": (
                    "userEnteredFormat.textFormat,"
                    "userEnteredFormat.verticalAlignment,"
                    "userEnteredFormat.wrapStrategy"
                ),
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": row_count,
                },
                "properties": {"pixelSize": 14},
                "fields": "pixelSize",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.07, "green": 0.09, "blue": 0.15},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                        "textFormat": {
                            "bold": True,
                            "fontFamily": "Arial",
                            "fontSize": 7,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 18},
                "fields": "pixelSize",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {
                        "frozenRowCount": 1,
                        "frozenColumnCount": 2,
                        "hideGridlines": True,
                    },
                },
                "fields": (
                    "gridProperties.frozenRowCount,"
                    "gridProperties.frozenColumnCount,"
                    "gridProperties.hideGridlines"
                ),
            }
        },
    ]
    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def history_checkbox_requests(
    ws,
    headers: List[str],
    start_row: int,
    end_row: int,
) -> List[dict]:
    """Build checkbox validation requests for inclusive sheet row numbers."""
    requests = []
    if end_row < start_row:
        return requests
    for name in ("Test Mode", "Archive"):
        if name not in headers:
            continue
        column = headers.index(name)
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        })
    return requests


def contiguous_row_runs(row_numbers: List[int]) -> List[Tuple[int, int]]:
    """Collapse sorted sheet row numbers into inclusive contiguous ranges."""
    if not row_numbers:
        return []
    runs = []
    run_start = previous = row_numbers[0]
    for row_number in row_numbers[1:]:
        if row_number == previous + 1:
            previous = row_number
            continue
        runs.append((run_start, previous))
        run_start = previous = row_number
    runs.append((run_start, previous))
    return runs


def format_history_status_rows(
    ws,
    headers: List[str],
    row_status: Dict[int, Tuple[Optional[bool], Optional[bool]]],
):
    """Color full history rows by test/archive status using muted pastels."""
    if not row_status or not headers:
        return
    neutral = {"red": 1.00, "green": 1.00, "blue": 1.00}
    test_green = {"red": 0.90, "green": 0.95, "blue": 0.92}
    archive_red = {"red": 0.96, "green": 0.90, "blue": 0.90}

    colored_rows = []
    for row_number in sorted(row_status):
        test_mode, archived = row_status[row_number]
        # Archive checked has priority over the test color.
        color = (
            archive_red
            if archived is True else
            test_green
            if test_mode is True else
            neutral
        )
        colored_rows.append((row_number, color))

    requests = []
    run_start, run_color = colored_rows[0]
    previous_row = run_start
    for row_number, color in colored_rows[1:] + [(None, None)]:
        if row_number == previous_row + 1 and color == run_color:
            previous_row = row_number
            continue
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": run_start - 1,
                    "endRowIndex": previous_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(headers),
                },
                "cell": {
                    "userEnteredFormat": {"backgroundColor": run_color}
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
        if row_number is None:
            break
        run_start = row_number
        previous_row = row_number
        run_color = color
    if requests:
        google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def ensure_history_control_columns(ws, headers: List[str]) -> List[str]:
    """Add reversible benchmark controls without changing historical metrics."""
    required = [
        "Timestamp", "WorldStructure Name", "Sample Count", "Block Hash",
        "Test Mode", "Description", "Archive",
    ]
    changed = False
    for name in required:
        if name not in headers:
            headers.append(name)
            changed = True
    if changed:
        # Keep editable metadata in A:G. The periodic control sync can then
        # read only these seven columns instead of the complete metric table.
        headers[:] = normalize_history_schema(ws, headers)
        end = gspread.utils.rowcol_to_a1(1, len(headers))
        google_api_call(
            ws.update,
            values=[headers],
            range_name=f"A1:{end}",
            value_input_option="RAW",
        )

    escaped_title = ws.title.replace("'", "''")
    control_names = (
        "Timestamp", "Block Hash", "Sample Count", "Test Mode", "Archive"
    )
    control_ranges = []
    for name in control_names:
        column = headers.index(name) + 1
        letter = gspread.utils.rowcol_to_a1(1, column).rstrip("1")
        control_ranges.append(f"'{escaped_title}'!{letter}1:{letter}")
    control_batch = google_api_call(
        ws.spreadsheet.values_batch_get,
        ranges=control_ranges,
    )
    value_ranges = control_batch.get("valueRanges", [])
    while len(value_ranges) < len(control_names):
        value_ranges.append({"values": []})

    def batch_column_values(index: int) -> List[object]:
        return [
            row[0] if row else ""
            for row in value_ranges[index].get("values", [])
        ]

    timestamp_values = batch_column_values(0)
    hash_values = batch_column_values(1)
    last_data_row = max(1, len(timestamp_values), len(hash_values))
    populated_rows = []
    for row_number in range(2, last_data_row + 1):
        timestamp = (
            timestamp_values[row_number - 1]
            if row_number <= len(timestamp_values) else ""
        )
        benchmark_hash = (
            hash_values[row_number - 1]
            if row_number <= len(hash_values) else ""
        )
        if str(timestamp).strip() or str(benchmark_hash).strip():
            populated_rows.append(row_number)

    requests = []

    # Empty grid rows must stay completely blank: no checkbox, no FALSE value,
    # and no status background. A checkbox is added only when a benchmark row
    # is actually written.
    empty_control_ranges = []
    populated_row_set = set(populated_rows)
    empty_rows = [
        row_number
        for row_number in range(2, ws.row_count + 1)
        if row_number not in populated_row_set
    ]
    for empty_start, empty_end in contiguous_row_runs(empty_rows):
        for name in ("Test Mode", "Archive"):
            column = headers.index(name)
            requests.append({
                "setDataValidation": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": empty_start - 1,
                        "endRowIndex": empty_end,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    }
                }
            })
            letter = gspread.utils.rowcol_to_a1(1, column + 1).rstrip("1")
            empty_control_ranges.append(
                f"{letter}{empty_start}:{letter}{empty_end}"
            )
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": empty_start - 1,
                    "endRowIndex": empty_end,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(headers),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 1.0,
                            "green": 1.0,
                            "blue": 1.0,
                        }
                    }
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    # Rewrite populated controls as native booleans and install their checkbox
    # validation in the same update. Grouping contiguous rows avoids creating
    # two API requests per benchmark and reliably repairs large histories.
    control_values = {
        "Test Mode": (headers.index("Test Mode"), batch_column_values(3)),
        "Archive": (headers.index("Archive"), batch_column_values(4)),
    }
    checkbox_rule = {
        "condition": {"type": "BOOLEAN"},
        "strict": True,
        "showCustomUi": True,
    }
    for name in ("Test Mode", "Archive"):
        column, values = control_values[name]
        for run_start, run_end in contiguous_row_runs(populated_rows):
            rows = []
            for row_number in range(run_start, run_end + 1):
                value = values[row_number - 1] if row_number <= len(values) else ""
                rows.append({"values": [{
                    "userEnteredValue": {
                        "boolValue": config_bool(value, False)
                    },
                    "dataValidation": checkbox_rule,
                }]})
            requests.append({
                "updateCells": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": run_start - 1,
                        "endRowIndex": run_end,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "rows": rows,
                    "fields": "userEnteredValue,dataValidation",
                }
            })

    # Legacy schema rewrites also stored Sample Count as text. Normalize it to
    # the same numeric type used by newly appended benchmark rows.
    sample_column = headers.index("Sample Count")
    sample_values = batch_column_values(2)
    for run_start, run_end in contiguous_row_runs(populated_rows):
        rows = []
        for row_number in range(run_start, run_end + 1):
            value = (
                sample_values[row_number - 1]
                if row_number <= len(sample_values) else ""
            )
            text = str(value).strip().lstrip("'").strip()
            try:
                number = float(text)
                native_value = {
                    "numberValue": int(number) if number.is_integer() else number
                }
            except (TypeError, ValueError):
                native_value = {"stringValue": text}
            rows.append({"values": [{"userEnteredValue": native_value}]})
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": run_start - 1,
                    "endRowIndex": run_end,
                    "startColumnIndex": sample_column,
                    "endColumnIndex": sample_column + 1,
                },
                "rows": rows,
                "fields": "userEnteredValue",
            }
        })
    if requests:
        google_api_call(ws.spreadsheet.batch_update, {"requests": requests})
    if empty_control_ranges:
        google_api_call(ws.batch_clear, empty_control_ranges)
    if populated_rows:
        print(
            "[OK] Benchmark History checkboxes normalized: "
            f"{len(populated_rows)} populated row(s)."
        )
    return headers
