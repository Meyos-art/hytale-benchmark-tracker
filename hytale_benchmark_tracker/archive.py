from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import gspread

from .analysis import analysis_header, world_sheet_title
from .config import config_bool
from .constants import ARCHIVE_HEADERS
from .google_api import _extended_value, ensure_worksheet, google_api_call
from .history import format_history_status_rows


def ensure_archive_sheet(book, cfg: dict):
    """Create the lightweight archive index and migrate its previous layout."""
    archive_title = str(cfg.get("archive_sheet", "") or "Benchmark Archive").strip()
    ws = ensure_worksheet(book, archive_title, rows=2000, cols=6)
    current = google_api_call(ws.row_values, 1)
    if current != ARCHIVE_HEADERS:
        old_rows = google_api_call(ws.get, range_name="A2:I") if current else []
        migrated_rows = []
        for row in old_rows:
            padded = row + [""] * max(0, len(current) - len(row))
            old = {
                name: padded[index] if index < len(padded) else ""
                for index, name in enumerate(current)
            }
            identity_fields = (
                "Timestamp",
                "Benchmark Date",
                "WorldStructure Name",
                "World Structure",
                "Sample Count",
                "Block Hash",
                "Description",
                "Benchmark History Link",
            )
            # Old sheet-wide checkbox values can make otherwise empty rows
            # look populated to the Values API. They are not archive records.
            if not any(str(old.get(name, "")).strip() for name in identity_fields):
                continue
            migrated_rows.append([
                old.get("Timestamp", old.get("Benchmark Date", "")),
                old.get("WorldStructure Name", old.get("World Structure", "")),
                old.get("Sample Count", ""),
                old.get("Block Hash", ""),
                old.get("Description", ""),
                old.get("Benchmark History Link", ""),
            ])
        clear_last_row = max(2, len(old_rows) + 1)
        google_api_call(ws.batch_clear, [f"A1:I{clear_last_row}"])
        values = [ARCHIVE_HEADERS] + migrated_rows
        google_api_call(
            ws.update,
            values=values,
            range_name=f"A1:F{len(values)}",
            value_input_option="RAW",
        )
    if ws.col_count != len(ARCHIVE_HEADERS):
        google_api_call(ws.spreadsheet.batch_update, {"requests": [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {"columnCount": len(ARCHIVE_HEADERS)},
                },
                "fields": "gridProperties.columnCount",
            }
        }]})
    requests = [
        {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(ARCHIVE_HEADERS)},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.07, "green": 0.09, "blue": 0.15},
                    "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 7, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                }},
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": ws.row_count, "startColumnIndex": 0, "endColumnIndex": len(ARCHIVE_HEADERS)},
                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Arial", "fontSize": 6}, "wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": 1, "endIndex": ws.row_count},
                "properties": {"pixelSize": 14, "hiddenByUser": False},
                "fields": "pixelSize,hiddenByUser",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1, "hideGridlines": False}},
                "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines",
            }
        },
    ]
    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})
    return ws


def update_named_fields(
    ws,
    headers: List[str],
    row: int,
    fields: Dict[str, object],
    pending_requests: Optional[List[dict]] = None,
):
    """Update several named cells in one Google Sheets request."""
    requests = []
    for name, value in fields.items():
        if name not in headers:
            continue
        column = headers.index(name)
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "rows": [{"values": [{"userEnteredValue": _extended_value(value)}]}],
                "fields": "userEnteredValue",
            }
        })
    if pending_requests is not None:
        pending_requests.extend(requests)
    elif requests:
        google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def set_analysis_column_state(
    ws,
    column: int,
    header: str,
    archived: bool,
    test_mode: bool,
    description: Optional[str] = None,
):
    """Apply metadata, title color, and visibility to one benchmark column."""
    title_color = (
        {"red": 0.07, "green": 0.48, "blue": 0.28}
        if test_mode else
        {"red": 0.07, "green": 0.09, "blue": 0.15}
    )
    values = [
        {"userEnteredValue": {"stringValue": header}},
        {"userEnteredValue": _extended_value(description)} if description is not None else {},
        {"userEnteredValue": {"boolValue": archived}},
    ]
    fields = "userEnteredValue"
    if description is None:
        fields = "userEnteredValue"
        values[1] = {"userEnteredValue": _extended_value("")}
    requests = [
        {
            "updateCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 3,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "rows": [{"values": [value]} for value in values],
                "fields": fields,
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": title_color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": column,
                    "endIndex": column + 1,
                },
                "properties": {"hiddenByUser": archived},
                "fields": "hiddenByUser",
            }
        },
    ]
    google_api_call(ws.spreadsheet.batch_update, {"requests": requests})


def google_sheet_range(title: str, cells: str) -> str:
    """Build an escaped A1 range for a worksheet title."""
    return f"'{title.replace(chr(39), chr(39) * 2)}'!{cells}"


def cache_analysis_header(state: dict, column: int, value: str):
    """Safely mirror a Google Sheets header in the local analysis cache."""
    headers = state.setdefault("headers", [])
    if not isinstance(headers, list):
        headers = list(headers)
        state["headers"] = headers
    if len(headers) <= column:
        headers.extend([""] * (column + 1 - len(headers)))
    headers[column] = value


def benchmark_history_link(cfg: dict, history_ws, row_number: int) -> str:
    """Build the archive hyperlink to one permanent history row."""
    return (
        f'=HYPERLINK("https://docs.google.com/spreadsheets/d/'
        f'{cfg["spreadsheet_id"]}/edit#gid={history_ws.id}&range=A{row_number}",'
        f'"Benchmark History row {row_number}")'
    )


def unique_analysis_states(analysis_cache: dict) -> List[tuple]:
    """Return each cached analysis worksheet once, regardless of cache aliases."""
    unique = []
    seen_sheet_ids = set()
    for analysis_ws, state in analysis_cache.values():
        if analysis_ws.id in seen_sheet_ids:
            continue
        unique.append((analysis_ws, state))
        seen_sheet_ids.add(analysis_ws.id)
    return unique


def index_history_controls(
    history_values: List[List[object]],
    history_headers: List[str],
    history_sheet_title: str,
) -> Tuple[dict, dict, dict, Dict[int, Tuple[Optional[bool], Optional[bool]]]]:
    """Index editable history metadata from the compact A:G control range."""
    columns = {name: index for index, name in enumerate(history_headers)}
    by_hash = {}
    candidates = {}
    row_status: Dict[int, Tuple[Optional[bool], Optional[bool]]] = {}
    for row_number, row in enumerate(history_values[1:], start=2):
        padded = row + [""] * (len(history_headers) - len(row))
        test_raw = padded[columns["Test Mode"]]
        archive_raw = padded[columns["Archive"]]
        row_status[row_number] = (
            config_bool(test_raw, False)
            if str(test_raw).strip() else None,
            config_bool(archive_raw, False)
            if str(archive_raw).strip() else None,
        )
        benchmark_hash = padded[columns["Block Hash"]]
        if benchmark_hash:
            by_hash[benchmark_hash] = (row_number, padded)
        world = padded[columns["WorldStructure Name"]]
        timestamp = analysis_header(padded[columns["Timestamp"]])
        title = world_sheet_title(world, history_sheet_title)
        candidates.setdefault((title, timestamp), []).append(benchmark_hash)
    return columns, by_hash, candidates, row_status


def sync_benchmark_controls(
    history_ws,
    history_state: dict,
    analysis_cache: dict,
    archive_ws,
    cfg: dict,
):
    """Synchronize editable archive, description, and test metadata."""
    history_headers = history_state["headers"]
    unique_analyses = unique_analysis_states(analysis_cache)

    # The editable metadata is always stored in the first seven canonical
    # columns. Reading the hundreds of immutable metric columns on every
    # refresh wasted quota and transferred most of the workbook repeatedly.
    history_control_last = gspread.utils.rowcol_to_a1(1, 7).rstrip("1")
    ranges = [
        google_sheet_range(history_ws.title, f"A1:{history_control_last}"),
        google_sheet_range(archive_ws.title, "A1:F"),
    ]
    for analysis_ws, state in unique_analyses:
        last_column = max(2, len(state["headers"]))
        last_letter = gspread.utils.rowcol_to_a1(1, last_column).rstrip("1")
        ranges.append(google_sheet_range(analysis_ws.title, f"A1:{last_letter}4"))
    batch = google_api_call(
        history_ws.spreadsheet.values_batch_get,
        ranges=ranges,
    )
    value_ranges = batch.get("valueRanges", [])
    while len(value_ranges) < len(ranges):
        value_ranges.append({"values": []})
    history_values = value_ranges[0].get("values", [])
    (
        history_col,
        history_by_hash,
        history_candidates,
        history_row_status,
    ) = index_history_controls(
        history_values,
        history_headers,
        cfg["history_sheet"],
    )

    archive_values = value_ranges[1].get("values", [])
    archive_headers = archive_values[0] if archive_values else ARCHIVE_HEADERS[:]
    archive_col = {name: index for index, name in enumerate(archive_headers)}
    archive_by_hash = {}
    archive_repair_requests = []
    archive_identity_headers = [
        name for name in (
            "Timestamp",
            "WorldStructure Name",
            "Sample Count",
            "Block Hash",
            "Description",
            "Benchmark History Link",
        )
        if name in archive_headers
    ]
    occupied_archive_rows = set()
    ghost_archive_rows = []
    claimed_archive_hashes = set()
    archive_rows = []
    for row_number, row in enumerate(archive_values[1:], start=2):
        padded = row + [""] * (len(archive_headers) - len(row))
        has_identity = any(
            str(padded[archive_col[name]]).strip()
            for name in archive_identity_headers
        )
        if not has_identity:
            if any(str(value).strip() for value in padded):
                ghost_archive_rows.append(row_number)
            continue
        archive_rows.append((row_number, padded))
        occupied_archive_rows.add(row_number)
        benchmark_hash = padded[archive_col["Block Hash"]]
        if benchmark_hash:
            claimed_archive_hashes.add(benchmark_hash)

    if ghost_archive_rows:
        clear_ranges = []
        range_start = range_end = ghost_archive_rows[0]
        for row_number in ghost_archive_rows[1:]:
            if row_number == range_end + 1:
                range_end = row_number
                continue
            clear_ranges.append(f"A{range_start}:F{range_end}")
            range_start = range_end = row_number
        clear_ranges.append(f"A{range_start}:F{range_end}")
        google_api_call(archive_ws.batch_clear, clear_ranges)

    for row_number, padded in archive_rows:
        benchmark_hash = padded[archive_col["Block Hash"]]
        if not benchmark_hash:
            archive_world = str(padded[archive_col["WorldStructure Name"]])
            archive_date = analysis_header(
                padded[archive_col["Timestamp"]]
            )
            candidates = history_candidates.get((archive_world, archive_date), [])
            benchmark_hash = next(
                (item for item in candidates if item and item not in claimed_archive_hashes),
                "",
            )
            if benchmark_hash and benchmark_hash in history_by_hash:
                matched_history_row, matched_history_data = history_by_hash[benchmark_hash]
                migration = {
                    "Timestamp": matched_history_data[history_col["Timestamp"]],
                    "WorldStructure Name": matched_history_data[
                        history_col["WorldStructure Name"]
                    ],
                    "Sample Count": matched_history_data[
                        history_col["Sample Count"]
                    ],
                    "Block Hash": benchmark_hash,
                    "Description": str(
                        matched_history_data[history_col["Description"]]
                    ),
                    "Benchmark History Link": benchmark_history_link(
                        cfg, history_ws, matched_history_row
                    ),
                }
                update_named_fields(
                    archive_ws,
                    archive_headers,
                    row_number,
                    migration,
                    archive_repair_requests,
                )
                for name, value in migration.items():
                    padded[archive_col[name]] = value
                claimed_archive_hashes.add(benchmark_hash)
        if benchmark_hash:
            if benchmark_hash in history_by_hash:
                matched_history_row, matched_history_data = history_by_hash[benchmark_hash]
                expected = {
                    "Timestamp": matched_history_data[history_col["Timestamp"]],
                    "WorldStructure Name": matched_history_data[
                        history_col["WorldStructure Name"]
                    ],
                    "Sample Count": matched_history_data[
                        history_col["Sample Count"]
                    ],
                    "Description": str(
                        matched_history_data[history_col["Description"]]
                    ),
                    "Benchmark History Link": benchmark_history_link(
                        cfg, history_ws, matched_history_row
                    ),
                }
                changes = {}
                for name, value in expected.items():
                    current = str(padded[archive_col[name]]).strip()
                    if name == "Benchmark History Link":
                        link_is_valid = (
                            current.startswith("Benchmark History row ")
                            or current.startswith("http://")
                            or current.startswith("https://")
                        )
                        if not link_is_valid:
                            changes[name] = value
                    elif not current:
                        changes[name] = value
                if changes:
                    update_named_fields(
                        archive_ws,
                        archive_headers,
                        row_number,
                        changes,
                        archive_repair_requests,
                    )
                    for name, value in changes.items():
                        padded[archive_col[name]] = value
            archive_by_hash[benchmark_hash] = (row_number, padded)

    if archive_repair_requests:
        google_api_call(
            archive_ws.spreadsheet.batch_update,
            {"requests": archive_repair_requests},
        )

    next_archive_row = 2

    used_hashes = set()
    history_updates = []
    all_title_color_updates = []
    for analysis_index, (analysis_ws, state) in enumerate(unique_analyses, start=2):
        if len(state["headers"]) <= 2:
            continue
        last_column = len(state["headers"])
        last_letter = gspread.utils.rowcol_to_a1(1, last_column).rstrip("1")
        metadata = value_ranges[analysis_index].get("values", [])
        while len(metadata) < 4:
            metadata.append([""] * last_column)
        for row in metadata:
            row.extend([""] * (last_column - len(row)))
        cached_test_states = state.setdefault("test_states", {})

        for column in range(2, last_column):
            raw_header = str(metadata[0][column]).strip()
            if not raw_header:
                continue
            visible_header = raw_header.removeprefix("ARCHIVED | ")
            benchmark_hash = str(metadata[3][column]).strip()
            if not benchmark_hash:
                candidates = history_candidates.get((analysis_ws.title, visible_header), [])
                benchmark_hash = next((item for item in candidates if item not in used_hashes), "")
                if benchmark_hash:
                    cell = gspread.utils.rowcol_to_a1(4, column + 1)
                    google_api_call(
                        analysis_ws.update,
                        values=[[benchmark_hash]],
                        range_name=cell,
                        value_input_option="RAW",
                    )
                    metadata[3][column] = benchmark_hash
                    state.setdefault("hashes", set()).add(benchmark_hash)
            if not benchmark_hash or benchmark_hash not in history_by_hash:
                continue
            used_hashes.add(benchmark_hash)

            history_row, history_data = history_by_hash[benchmark_hash]
            history_link = benchmark_history_link(cfg, history_ws, history_row)
            history_test = config_bool(history_data[history_col["Test Mode"]], False)
            history_description = str(history_data[history_col["Description"]])
            is_archived = raw_header.startswith("ARCHIVED | ")
            history_archive_raw = history_data[history_col["Archive"]]
            history_archive_explicit = str(history_archive_raw).strip() != ""
            history_archived = config_bool(
                history_archive_raw,
                is_archived,
            )
            final_archived: Optional[bool] = (
                history_archived if history_archive_explicit else None
            )
            archive_entry = archive_by_hash.get(benchmark_hash)

            analysis_description = str(metadata[1][column])
            archive_raw = metadata[2][column]
            archive_is_explicit = str(archive_raw).strip() != ""
            analysis_archived = config_bool(archive_raw, False)
            analysis_test = history_test

            if cached_test_states.get(benchmark_hash) != analysis_test:
                title_color = (
                    {"red": 0.07, "green": 0.48, "blue": 0.28}
                    if analysis_test else
                    {"red": 0.07, "green": 0.09, "blue": 0.15}
                )
                all_title_color_updates.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": analysis_ws.id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": column,
                            "endColumnIndex": column + 1,
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": title_color}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                })
                cached_test_states[benchmark_hash] = analysis_test

            archive_requested = (
                not is_archived
                and (
                    analysis_archived
                    or history_archived
                )
            )
            restore_requested = (
                is_archived
                and (
                    (archive_is_explicit and not analysis_archived)
                    or not history_archived
                )
            )

            description = analysis_description or history_description
            missing_archive_record = is_archived and archive_entry is None
            if archive_requested or missing_archive_record:
                final_archived = True
                archive_record = {
                    "Timestamp": history_data[history_col["Timestamp"]],
                    "WorldStructure Name": history_data[
                        history_col["WorldStructure Name"]
                    ],
                    "Sample Count": history_data[history_col["Sample Count"]],
                    "Block Hash": benchmark_hash,
                    "Description": description,
                    "Benchmark History Link": history_link,
                }
                archive_row_requests = []
                if archive_entry:
                    archive_row = archive_entry[0]
                    update_named_fields(
                        archive_ws,
                        archive_headers,
                        archive_entry[0],
                        archive_record,
                        archive_row_requests,
                    )
                else:
                    row = [archive_record.get(name, "") for name in archive_headers]
                    while next_archive_row in occupied_archive_rows:
                        next_archive_row += 1
                    new_row = next_archive_row
                    occupied_archive_rows.add(new_row)
                    next_archive_row += 1
                    if new_row > archive_ws.row_count:
                        new_row_count = max(new_row, archive_ws.row_count + 1000)
                        google_api_call(archive_ws.spreadsheet.batch_update, {"requests": [{
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": archive_ws.id,
                                    "gridProperties": {"rowCount": new_row_count},
                                },
                                "fields": "gridProperties.rowCount",
                            }
                        }]})
                    archive_row = new_row
                    padded = row + [""] * (len(archive_headers) - len(row))
                    archive_by_hash[benchmark_hash] = (new_row, padded)
                    archive_row_requests.append({
                        "updateCells": {
                            "range": {
                                "sheetId": archive_ws.id,
                                "startRowIndex": new_row - 1,
                                "endRowIndex": new_row,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(archive_headers),
                            },
                            "rows": [{
                                "values": [
                                    {"userEnteredValue": _extended_value(value)}
                                    for value in row
                                ]
                            }],
                            "fields": "userEnteredValue",
                        }
                    })
                archive_row_requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": archive_ws.id,
                            "dimension": "ROWS",
                            "startIndex": archive_row - 1,
                            "endIndex": archive_row,
                        },
                        "properties": {"hiddenByUser": False, "pixelSize": 14},
                        "fields": "hiddenByUser,pixelSize",
                    }
                })
                google_api_call(
                    archive_ws.spreadsheet.batch_update,
                    {"requests": archive_row_requests},
                )
                update_named_fields(
                    history_ws,
                    history_headers,
                    history_row,
                    {
                        "Test Mode": analysis_test,
                        "Description": description,
                        "Archive": True,
                    },
                    history_updates,
                )
                set_analysis_column_state(
                    analysis_ws,
                    column,
                    f"ARCHIVED | {visible_header}",
                    True,
                    analysis_test,
                    description,
                )
                cache_analysis_header(
                    state,
                    column,
                    f"ARCHIVED | {visible_header}",
                )
                print(
                    f"[ARCHIVE] {analysis_ws.title} | {visible_header} "
                    f"-> Benchmark Archive row {archive_row}"
                )
            elif restore_requested:
                final_archived = False
                if archive_entry:
                    archive_data = archive_entry[1]
                    archive_row = archive_entry[0]
                    description = str(archive_data[archive_col["Description"]]) or description
                    google_api_call(
                        archive_ws.batch_clear,
                        [f"A{archive_row}:F{archive_row}"],
                    )
                    archive_by_hash.pop(benchmark_hash, None)
                    occupied_archive_rows.discard(archive_row)
                update_named_fields(
                    history_ws,
                    history_headers,
                    history_row,
                    {
                        "Test Mode": analysis_test,
                        "Description": description,
                        "Archive": False,
                    },
                    history_updates,
                )
                set_analysis_column_state(
                    analysis_ws,
                    column,
                    visible_header,
                    False,
                    analysis_test,
                    description,
                )
                cache_analysis_header(state, column, visible_header)
                print(f"[RESTORE] {analysis_ws.title} | {visible_header}")
            elif not is_archived:
                final_archived = False
                changes = {}
                if description != history_description:
                    changes["Description"] = description
                if history_archived or not history_archive_explicit:
                    changes["Archive"] = False
                if changes:
                    update_named_fields(
                        history_ws,
                        history_headers,
                        history_row,
                        changes,
                        history_updates,
                    )
            elif is_archived and (not archive_is_explicit or not history_archive_explicit):
                final_archived = True
                update_named_fields(
                    history_ws,
                    history_headers,
                    history_row,
                    {
                        "Test Mode": analysis_test,
                        "Description": description,
                        "Archive": True,
                    },
                    history_updates,
                )
                set_analysis_column_state(
                    analysis_ws,
                    column,
                    raw_header,
                    True,
                    analysis_test,
                    description,
                )
            history_row_status[history_row] = (
                analysis_test,
                final_archived,
            )
    if all_title_color_updates:
        google_api_call(
            history_ws.spreadsheet.batch_update,
            {"requests": all_title_color_updates},
        )
    if history_updates:
        google_api_call(
            history_ws.spreadsheet.batch_update,
            {"requests": history_updates},
        )
    status_signature = tuple(
        (row_number, status[0], status[1])
        for row_number, status in sorted(history_row_status.items())
    )
    if history_state.get("history_status_signature") != status_signature:
        format_history_status_rows(
            history_ws,
            history_headers,
            history_row_status,
        )
        history_state["history_status_signature"] = status_signature
