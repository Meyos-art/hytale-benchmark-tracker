from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from .config import cache_runtime_filters
from .constants import (
    GRID_RE, HISTORY_FIXED_STAGE_ORDER, HISTORY_MEMORY_DETAIL_ORDER,
    HISTORY_METADATA_ORDER, HISTORY_STAGE_DETAIL_ORDER, LINE_DATA_RE,
    MAJOR_NUMERIC_SECTIONS, MAJOR_TEXT_SECTIONS, MEMORY_GRID_INDEX_RE,
    PREFIX_RE, PROP_STAGE_RE, STAGE_HEADER_RE, STAGE_RE, STAGE_SUFFIX_RE,
    START_RE, WORLD_RE,
)


def strip_prefix(line: str) -> str:
    line = line.rstrip("\r\n")
    line = PREFIX_RE.sub("", line)
    # Leading tabs describe the report hierarchy.
    return line.rstrip()


def block_hash(lines: List[str]) -> str:
    raw = "\n".join(lines).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def extract_world_name(clean_lines: List[str]) -> Optional[str]:
    for line in clean_lines:
        m = WORLD_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def is_wanted_block(
    clean_lines: List[str],
    cfg: dict,
    world: Optional[str] = None,
) -> bool:
    if not clean_lines:
        return False

    m = START_RE.search(clean_lines[0])
    wanted_counts = cfg.get("_sample_count_set")
    if wanted_counts is None:
        wanted_counts = cache_runtime_filters(cfg)["_sample_count_set"]
    if not m or int(m.group(1)) not in wanted_counts:
        return False

    wanted = cfg.get("_world_structure_set")
    if wanted is None:
        wanted = cache_runtime_filters(cfg)["_world_structure_set"]
    if not wanted:
        return True

    if world is None:
        world = extract_world_name(clean_lines)
    return world in wanted


def canonical_stage_label(label: str) -> str:
    """Return the stable stage identity without its shifting global index."""
    return STAGE_SUFFIX_RE.sub("", str(label)).strip()


def canonical_history_metric_key(key: str) -> str:
    """Normalize stage path segments used as Benchmark History headers."""
    if str(key) == "In Analysis":
        return "Archive"
    parts = str(key).split(" / ")
    if parts and parts[0] in {"Content Generation", "Context Dependency Report"}:
        parts = [parts[0]] + [canonical_stage_label(part) for part in parts[1:]]
    return " / ".join(parts)


def history_stage_sort_key(parts: List[str], original_index: int) -> tuple:
    """Sort fixed stages and any number of PropStage categories consistently."""
    if len(parts) < 2:
        return (-1, 0, original_index)
    stage = parts[1]
    prop_match = PROP_STAGE_RE.fullmatch(stage)
    stage_order = (
        100 + int(prop_match.group(1))
        if prop_match else HISTORY_FIXED_STAGE_ORDER.get(stage, 9000)
    )
    detail_order = HISTORY_STAGE_DETAIL_ORDER.get(
        parts[2] if len(parts) > 2 else "",
        0,
    )
    return (stage_order, detail_order, original_index)


def history_header_sort_key(header: str, original_index: int) -> tuple:
    """Place every history field in its logical report section."""
    if header in HISTORY_METADATA_ORDER:
        return (0, HISTORY_METADATA_ORDER[header], 0, original_index)
    if header == "Total":
        return (1, 0, 0, original_index)

    parts = header.split(" / ")
    section = parts[0]
    if section == "Content Generation":
        stage_key = history_stage_sort_key(parts, original_index)
        return (2, stage_key[0], stage_key[1], original_index)
    if section == "Data Transfer":
        return (3, 0, 0, original_index)
    if section == "Memory Usage Report":
        if len(parts) > 1 and parts[1] == "Buffers Memory Usage":
            return (4, -10000, 0, original_index)
        grid_match = MEMORY_GRID_INDEX_RE.search(header)
        grid_order = int(grid_match.group(1)) if grid_match else 9000
        detail_order = HISTORY_MEMORY_DETAIL_ORDER.get(
            parts[2] if len(parts) > 2 else "",
            0,
        )
        return (4, grid_order, detail_order, original_index)
    if section == "Context Dependency Report":
        stage_key = history_stage_sort_key(parts, original_index)
        return (5, stage_key[0], stage_key[1], original_index)
    if section == "Buffer Cache Report":
        return (6, 0, 0, original_index)
    if header == "Raw Benchmark":
        return (99, 0, 0, original_index)
    return (90, 0, 0, original_index)


def ordered_history_headers(headers: List[str]) -> List[str]:
    """Return canonical, unique history headers in stable report order."""
    unique: List[str] = []
    first_position: Dict[str, int] = {}
    for index, header in enumerate(headers):
        canonical = canonical_history_metric_key(header)
        if canonical not in first_position:
            first_position[canonical] = index
            unique.append(canonical)
    return sorted(
        unique,
        key=lambda header: history_header_sort_key(
            header,
            first_position[header],
        ),
    )


def parse_block(
    raw_lines: List[str],
    clean_lines: Optional[List[str]] = None,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    """Return flattened history data and hierarchical analysis rows."""
    clean = clean_lines if clean_lines is not None else [strip_prefix(x) for x in raw_lines]
    flat: Dict[str, object] = {}
    display_rows: List[Dict[str, object]] = []

    current_stage: Optional[str] = None
    current_grid: Optional[str] = None
    current_major: Optional[str] = None
    major_indent = 0
    spacer_count = 0

    def add_display_row(key: str, label: str, value="", unit="", level=0, kind="metric"):
        display_rows.append({
            "key": key,
            "label": ("\u00a0" * (4 * max(0, level))) + label,
            "value": value,
            "unit": unit,
            "kind": kind,
        })

    for raw_line in clean:
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip("\t "))
        line = raw_line.lstrip("\t ")

        # WorldStructure Name contains text rather than a numeric value.
        wm = WORLD_RE.search(line)
        if wm:
            world = wm.group(1).strip()
            flat["WorldStructure Name"] = world
            add_display_row("meta:world", "World Structure Name", world, kind="meta")
            continue

        # Sample Count metadata.
        sm = START_RE.search(line)
        if sm:
            value = int(sm.group(1))
            flat["Sample Count"] = value
            add_display_row("meta:samples", "Sample Count", value, kind="meta")
            continue

        # Visually separate the five major report sections.
        section_data = LINE_DATA_RE.match(line)
        section_name = section_data.group("label").strip() if section_data else line.rstrip(":")
        if section_name in MAJOR_NUMERIC_SECTIONS or section_name in MAJOR_TEXT_SECTIONS:
            if current_major is not None:
                spacer_count += 1
                add_display_row(f"spacer:{spacer_count}", "\u200b" * spacer_count, kind="spacer")
            current_major = section_name
            major_indent = indent
            current_stage = None
            current_grid = None
            value = section_data.group("data").strip() if section_data else ""
            if section_data:
                flat[section_name] = value
            add_display_row(f"section:{section_name}", section_name, value, kind="section")
            continue

        grid_m = GRID_RE.match(line)
        if grid_m:
            current_grid = grid_m.group("label")
            current_stage = None
            level = max(1, indent - major_indent) if current_major else indent
            add_display_row(
                f"grid:{current_major}:{current_grid}",
                current_grid,
                level=level,
                kind="subcategory",
            )
            continue

        stage_m = STAGE_RE.match(line)
        if stage_m:
            current_stage = canonical_stage_label(stage_m.group("label"))
            value = stage_m.group("data").strip()
            flat[f"{current_major} / {current_stage}"] = value
            level = max(1, indent - major_indent) if current_major else indent
            add_display_row(
                f"stage:{current_major}:{current_stage}",
                current_stage,
                value,
                "",
                level,
                "subcategory",
            )
            continue

        stage_header_m = STAGE_HEADER_RE.match(line)
        if stage_header_m:
            current_stage = canonical_stage_label(stage_header_m.group("label"))
            level = max(1, indent - major_indent) if current_major else indent
            add_display_row(
                f"stage:{current_major}:{current_stage}",
                current_stage,
                level=level,
                kind="subcategory",
            )
            continue

        # Preserve everything after the first colon exactly as log data.
        # Examples: "3.281 ms", "640", "21.10 %", "{x=10, z=10}".
        data_m = section_data
        if data_m:
            label = data_m.group("label").strip()
            value = data_m.group("data").strip()
            context = current_stage if current_stage else current_grid
            path_parts = [part for part in (current_major, context, label) if part]
            unique = " / ".join(path_parts)
            flat[unique] = value
            level = max(1, indent - major_indent) if current_major else indent
            if label == "Total":
                add_display_row("summary:total", "Total", value, level=level, kind="total")
            else:
                context = current_stage or current_grid or current_major or "general"
                add_display_row(
                    f"metric:{current_major}:{context}:{label}",
                    label,
                    value,
                    level=level,
                )
            continue

        # Preserve useful non-numeric lines such as unrecognized headings.
        level = max(1, indent - major_indent) if current_major else indent
        context = current_stage or current_grid or current_major or "general"
        add_display_row(f"text:{current_major}:{context}:{line}", line, level=level)

    return flat, display_rows
