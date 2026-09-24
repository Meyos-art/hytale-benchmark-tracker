from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .analysis import (
    BASIC_WORLD_NAME, add_comparison_column, add_detail_groups, analysis_header,
    apply_basic_reference_column, apply_basic_reference_to_all,
    basic_reference_from_rows, basic_reference_identity,
    apply_analysis_time_filter, default_analysis_controls,
    get_world_analysis_state, has_basic_reference_column,
    load_existing_analysis_sheets,
    load_latest_basic_reference,
)
from .archive import ensure_archive_sheet, sync_benchmark_controls, unique_analysis_states
from .config import (
    ensure_config_sheet, parse_resume_datetime, read_sheet_config,
    resolve_log_folder, update_basic_reference_status,
    validate_log_file_patterns,
)
from .constants import END_RE, START_RE
from .google_api import (
    ensure_spreadsheet_locale, ensure_worksheet, google_api_call, google_client,
)
from .history import (
    ensure_history_control_columns, format_history_layout,
    normalize_history_schema, upsert_history,
)
from .parser import (
    block_hash, extract_world_name, is_wanted_block, parse_block, strip_prefix,
)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # Windows may briefly lock the state file through antivirus or indexing.
    # Retry before giving up.
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5)


def resolve_log_paths(cfg: dict) -> List[Path]:
    """Resolve configured file names and patterns to existing log files."""
    folder = resolve_log_folder(cfg)
    paths: List[Path] = []
    for name in validate_log_file_patterns(cfg.get("log_files", [])):
        if any(char in name for char in "*?["):
            candidates = (path for path in folder.glob(name) if path.is_file())
        else:
            path = folder / name
            candidates = [path] if path.is_file() else []
        for candidate in candidates:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(folder):
                raise ValueError(
                    f"Log file resolves outside the configured folder: {name}"
                )
            paths.append(resolved)
    return sorted(set(paths), key=lambda path: str(path).casefold())


def block_timestamp(block: List[str]) -> Tuple[str, datetime]:
    text = block[0] if block else ""
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)",
        text,
    )
    if match:
        value = match.group(1)
        return value, datetime.fromisoformat(value)
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S"), now


def process_completed_block(
    book,
    history_ws,
    history_state: dict,
    analysis_cache: dict,
    block: List[str],
    cfg: dict,
    basic_reference_state: dict,
    config_ws,
    base_cfg: dict,
):
    clean_block = [strip_prefix(line) for line in block]
    timestamp, timestamp_value = block_timestamp(block)
    resume_from = cfg.get("_resume_datetime")
    if resume_from is None and cfg.get("resume_from"):
        resume_from = parse_resume_datetime(cfg["resume_from"])
    if resume_from and timestamp_value < resume_from:
        print(f"[SKIP TEST] {timestamp} is earlier than resume date {resume_from}")
        return

    world = extract_world_name(clean_block)
    if not is_wanted_block(clean_block, cfg, world):
        sample_match = START_RE.search(clean_block[0]) if clean_block else None
        sample_count = sample_match.group(1) if sample_match else "unknown"
        expected = cfg.get("_sample_counts_label", "all")
        print(
            f"[SKIP] Block ignored | config={world!r} | "
            f"Sample Count={sample_count} (expected: {expected})"
        )
        return

    flat, display_rows = parse_block(block, clean_block)
    benchmark_hash = block_hash(block)
    world = str(flat.get("WorldStructure Name", world or ""))
    record = {
        "Timestamp": timestamp,
        "WorldStructure Name": world,
        "Sample Count": flat.get("Sample Count", ""),
        "Block Hash": benchmark_hash,
        "Test Mode": bool(cfg.get("test_mode", False)),
        "Description": "",
        "Archive": False,
    }
    record.update(flat)
    record["Raw Benchmark"] = "".join(block).rstrip("\r\n")

    history_result = upsert_history(history_ws, record, history_state)
    analysis_ws, analysis_state = get_world_analysis_state(
        book,
        world,
        cfg["history_sheet"],
        analysis_cache,
        cfg,
    )
    analysis_timestamp = analysis_header(timestamp)
    # The block hash stays unique even when two benchmarks share a second or
    # when an existing column is currently archived and hidden.
    analysis_added = benchmark_hash not in analysis_state.get("hashes", set())
    if analysis_added:
        add_comparison_column(
            analysis_ws,
            analysis_timestamp,
            display_rows,
            analysis_state,
            cfg,
            benchmark_hash,
            bool(cfg.get("test_mode", False)),
        )

    if not analysis_state.get("groups_updated", False):
        add_detail_groups(analysis_ws, display_rows)
        analysis_state["groups_updated"] = True

    current_reference = basic_reference_state.get("reference")
    if world.casefold() == BASIC_WORLD_NAME.casefold():
        candidate = basic_reference_from_rows(
            analysis_timestamp,
            benchmark_hash,
            display_rows,
        )
        current_time = (
            datetime.fromisoformat(current_reference["timestamp"])
            if current_reference else None
        )
        candidate_time = datetime.fromisoformat(candidate["timestamp"])
        if (
            (current_time is None or candidate_time >= current_time)
            and basic_reference_identity(candidate)
            != basic_reference_identity(current_reference)
        ):
            basic_reference_state["reference"] = candidate
            apply_basic_reference_to_all(analysis_cache, candidate)
            update_basic_reference_status(config_ws, base_cfg, candidate)
            print(f"[BASIC] Static reference updated: {candidate['timestamp']}")
    elif not has_basic_reference_column(analysis_state):
        # A newly-created world sheet receives the selected reference without
        # rewriting all other analysis sheets.
        apply_basic_reference_column(
            analysis_ws,
            analysis_state,
            current_reference,
        )

    if history_result == "added" or analysis_added:
        print(f"[ADD] {timestamp} | {world} | hash={benchmark_hash[:8]}")
    else:
        print(f"[SKIP] Duplicate {benchmark_hash[:8]}")


def process_stream(cfg: dict):
    state_path = Path(cfg["state_file"]).expanduser()
    gc = google_client(cfg["service_account_json"])
    book = google_api_call(gc.open_by_key, cfg["spreadsheet_id"])
    ensure_spreadsheet_locale(book)
    config_ws = ensure_config_sheet(book, cfg)
    runtime_cfg = read_sheet_config(config_ws, cfg)
    history_ws = ensure_worksheet(book, cfg["history_sheet"])
    archive_ws = ensure_archive_sheet(book, cfg)

    # Read Google Sheets once at startup, then maintain local caches after each
    # write instead of repeatedly loading entire columns.
    history_headers = google_api_call(history_ws.row_values, 1)
    history_headers = normalize_history_schema(history_ws, history_headers)
    history_headers = ensure_history_control_columns(history_ws, history_headers)
    hash_column = (
        history_headers.index("Block Hash") + 1
        if "Block Hash" in history_headers
        else 4
    )
    history_hashes = (
        google_api_call(history_ws.col_values, hash_column)[1:]
        if history_headers
        else []
    )
    history_hash_rows = {
        value: row_index
        for row_index, value in enumerate(history_hashes, start=2)
        if value
    }
    history_state = {
        "headers": history_headers,
        "hash_rows": history_hash_rows,
        "next_row": max(history_hash_rows.values(), default=1) + 1,
        "grid_rows": history_ws.row_count,
        "grid_columns": history_ws.col_count,
        # Existing rows keep their values; the control columns are managed by
        # the archive synchronization below.
        "managed_start": len(history_headers),
    }
    format_history_layout(history_ws)
    analysis_cache: Dict[str, tuple] = {}
    load_existing_analysis_sheets(book, runtime_cfg, analysis_cache)
    sync_benchmark_controls(
        history_ws,
        history_state,
        analysis_cache,
        archive_ws,
        runtime_cfg,
    )
    basic_reference_state = {
        "reference": load_latest_basic_reference(
            analysis_cache,
            runtime_cfg["history_sheet"],
        )
    }
    apply_basic_reference_to_all(
        analysis_cache,
        basic_reference_state["reference"],
    )
    update_basic_reference_status(
        config_ws,
        cfg,
        basic_reference_state["reference"],
    )
    if basic_reference_state["reference"]:
        print(
            "[BASIC] Static reference: "
            f"{basic_reference_state['reference']['timestamp']}"
        )
    else:
        print(
            "[SETUP] No active Basic benchmark found. Run a benchmark with "
            "WorldStructure Name Basic."
        )

    saved_state = load_state(state_path)
    positions = {
        str(path): int(position)
        for path, position in saved_state.get("positions", {}).items()
        if isinstance(position, int)
    }
    legacy_path = saved_state.get("log_file")
    legacy_position = saved_state.get("position")
    if legacy_path and isinstance(legacy_position, int):
        positions.setdefault(str(Path(legacy_path).resolve()), legacy_position)

    # Recover from a stale watcher state left by an older/broken version. If
    # the permanent history contains no benchmark while configured logs were
    # already marked as consumed, replay those logs from the beginning.
    if not history_hash_rows and not runtime_cfg.get("start_from_end", False):
        replayed = []
        for log_path in resolve_log_paths(runtime_cfg):
            key = str(log_path)
            if positions.get(key, 0) > 0:
                positions[key] = 0
                replayed.append(log_path.name)
        if replayed:
            print(
                "[RECOVERY] Benchmark History is empty; replaying: "
                + ", ".join(replayed)
            )
    contexts: Dict[str, dict] = {}
    log_paths = resolve_log_paths(runtime_cfg)

    print(f"[OK] Google Sheet : {book.title}")
    print(f"[OK] Configuration : sheet {config_ws.title}")
    print(f"[OK] Log profile   : {runtime_cfg['log_profile']}")
    print(f"[OK] Log folder    : {resolve_log_folder(runtime_cfg)}")
    print(f"[OK] Sample Count  : {', '.join(map(str, runtime_cfg['sample_counts']))}")
    wanted = runtime_cfg.get("world_structures") or []
    print(f"[OK] Config(s)     : {', '.join(wanted) if wanted else 'all'}")
    print("Press Ctrl+C to stop.\n")

    next_config_refresh = time.monotonic() + runtime_cfg["config_refresh_seconds"]
    collection_was_enabled: Optional[bool] = None
    missing_files_reported = False

    while True:
        if time.monotonic() >= next_config_refresh:
            try:
                updated_cfg = read_sheet_config(config_ws, cfg)
                sync_benchmark_controls(
                    history_ws,
                    history_state,
                    analysis_cache,
                    archive_ws,
                    updated_cfg,
                )
                refreshed_reference = load_latest_basic_reference(
                    analysis_cache,
                    updated_cfg["history_sheet"],
                    basic_reference_state.get("reference"),
                )
                if refreshed_reference != basic_reference_state.get("reference"):
                    basic_reference_state["reference"] = refreshed_reference
                    apply_basic_reference_to_all(
                        analysis_cache,
                        refreshed_reference,
                    )
                    update_basic_reference_status(
                        config_ws,
                        cfg,
                        refreshed_reference,
                    )
                    print(
                        "[BASIC] Static reference updated: "
                        + (
                            refreshed_reference["timestamp"]
                            if refreshed_reference else "missing"
                        )
                    )
                if (
                    updated_cfg.get("analysis_recent_days")
                    != runtime_cfg.get("analysis_recent_days")
                ):
                    filter_requests = []
                    for analysis_ws, analysis_state in unique_analysis_states(
                        analysis_cache
                    ):
                        apply_analysis_time_filter(
                            analysis_ws,
                            analysis_state["headers"],
                            default_analysis_controls(),
                            updated_cfg,
                            pending_requests=filter_requests,
                        )
                    if filter_requests:
                        google_api_call(
                            book.batch_update,
                            {"requests": filter_requests},
                        )
                runtime_cfg = updated_cfg
                log_paths = resolve_log_paths(runtime_cfg)
            except Exception as exc:
                print(f"[CONFIG] Invalid value; keeping the last valid configuration: {exc}")
            next_config_refresh = (
                time.monotonic() + runtime_cfg["config_refresh_seconds"]
            )

        enabled = bool(runtime_cfg.get("collection_enabled", True))
        if enabled != collection_was_enabled:
            print("[CONFIG] Collection enabled." if enabled else "[CONFIG] Collection paused.")
            collection_was_enabled = enabled
        if not enabled:
            time.sleep(float(runtime_cfg["poll_seconds"]))
            continue

        if not log_paths:
            if not missing_files_reported:
                print("[WAIT] No file matches the active log profile / log_files.")
                missing_files_reported = True
            time.sleep(float(runtime_cfg["poll_seconds"]))
            continue
        missing_files_reported = False

        state_changed = False
        for log_path in log_paths:
            key = str(log_path)
            try:
                size = log_path.stat().st_size
            except FileNotFoundError:
                continue
            if key not in contexts:
                initial_position = positions.get(key)
                if initial_position is None:
                    initial_position = (
                        size
                        if runtime_cfg.get("start_from_end", False)
                        else 0
                    )
                contexts[key] = {
                    "position": initial_position,
                    "collecting": False,
                    "block": [],
                }
                print(f"[OK] Watching: {log_path} | position={initial_position}")

            context = contexts[key]
            position = int(context["position"])
            if size < position:
                print(f"[INFO] Log truncated or rotated: {log_path.name}; restarting at the beginning.")
                position = 0
                context["collecting"] = False
                context["block"] = []

            with log_path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(position)
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    clean = strip_prefix(line)

                    if START_RE.search(clean):
                        context["collecting"] = True
                        context["block"] = [line]
                        continue

                    if context["collecting"]:
                        context["block"].append(line)
                        if END_RE.search(clean):
                            process_completed_block(
                                book,
                                history_ws,
                                history_state,
                                analysis_cache,
                                context["block"],
                                runtime_cfg,
                                basic_reference_state,
                                config_ws,
                                cfg,
                            )
                            context["collecting"] = False
                            context["block"] = []
                position = stream.tell()

            if position != context["position"]:
                context["position"] = position
                positions[key] = position
                state_changed = True

        if state_changed:
            save_state(state_path, {
                "positions": positions,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })

        time.sleep(float(runtime_cfg["poll_seconds"]))
