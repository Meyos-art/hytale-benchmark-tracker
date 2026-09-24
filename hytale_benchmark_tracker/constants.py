from __future__ import annotations

import re

START_RE = re.compile(r"Sample Count:\s*(\d+)", re.IGNORECASE)

WORLD_RE = re.compile(r"WorldStructure Name:\s*'?\s*([^'\r\n]+?)\s*'?\s*$", re.IGNORECASE)

END_RE = re.compile(r"Missed/Total Ratio:\s*([0-9.,]+)\s*%", re.IGNORECASE)

# Hytale release builds include a logger name between INFO and SERVER, while
# older pre-release builds leave that field empty. Accept both layouts.
PREFIX_RE = re.compile(r"^.*?\|INFO\|[^|\r\n]*\|SERVER\s*-\s*")

LINE_DATA_RE = re.compile(r"^(?P<label>.*?):\s*(?P<data>.+?)\s*$")

GRID_RE = re.compile(r"^(?P<label>.+?Grid\s*\(Index\s*-?\d+\)):\s*$", re.IGNORECASE)

STAGE_RE = re.compile(r"^(?P<label>.+?\(Stage\s*\d+\)):\s*(?P<data>.+?)\s*$", re.IGNORECASE)

STAGE_HEADER_RE = re.compile(r"^(?P<label>.+?\(Stage\s*\d+\)):\s*$", re.IGNORECASE)

STAGE_SUFFIX_RE = re.compile(r"\s*\(Stage\s*\d+\)\s*$", re.IGNORECASE)

STAGE_TOKEN_RE = re.compile(r"\s*\(Stage\s*\d+\)", re.IGNORECASE)

PROP_STAGE_RE = re.compile(r"PropStage(\d+)", re.IGNORECASE)

MEMORY_GRID_INDEX_RE = re.compile(r"\(Index\s*(-?\d+)\)", re.IGNORECASE)

MAJOR_NUMERIC_SECTIONS = {"Content Generation", "Data Transfer"}

MAJOR_TEXT_SECTIONS = {
    "Memory Usage Report",
    "Context Dependency Report",
    "Buffer Cache Report",
}

REQUIRED_SPREADSHEET_LOCALE = "en_US"

DEFAULT_LOG_PROFILE = "hytale_prerelease"

LOG_PROFILE_PATHS = {
    "hytale_prerelease": ("Hytale", "data", "pre-release", "Logs"),
    "hytale_release": ("Hytale", "UserData", "Logs"),
}

SUPPORTED_LOG_PROFILES = set(LOG_PROFILE_PATHS)

ARCHIVE_HEADERS = [
    "Timestamp",
    "WorldStructure Name",
    "Sample Count",
    "Block Hash",
    "Description",
    "Benchmark History Link",
]

HISTORY_METADATA_ORDER = {
    "Timestamp": 0,
    "WorldStructure Name": 1,
    "Sample Count": 2,
    "Block Hash": 3,
    "Test Mode": 4,
    "Description": 5,
    "Archive": 6,
}

HISTORY_FIXED_STAGE_ORDER = {
    "Access Initialization": 0,
    "BiomeStage": 10,
    "BiomeDistanceStage": 20,
    "TerrainStage": 30,
    "TintStage": 10000,
    "EnvironmentStage": 10010,
}

HISTORY_STAGE_DETAIL_ORDER = {
    "Preparation": 1,
    "Execution": 2,
    "Async Processes Start": 3,
    "Output Size (Buffer Column)": 1,
    "Output Size (Chunk Column)": 2,
}

HISTORY_MEMORY_DETAIL_ORDER = {
    "Memory Footprint": 1,
    "Buffer Count": 2,
}

ANALYSIS_ROW_STYLES = {
    "meta": {
        "backgroundColor": {"red": 0.97, "green": 0.98, "blue": 0.99},
        "textFormat": {
            "fontSize": 6,
            "foregroundColor": {"red": 0.28, "green": 0.33, "blue": 0.41},
        },
    },
    "total": {
        "backgroundColor": {"red": 0.89, "green": 0.92, "blue": 0.96},
        "textFormat": {"bold": True, "fontSize": 7},
    },
    "subcategory": {
        "backgroundColor": {"red": 0.95, "green": 0.96, "blue": 0.98},
        "textFormat": {
            "bold": True,
            "fontSize": 6,
            "foregroundColor": {"red": 0.20, "green": 0.25, "blue": 0.33},
        },
    },
}

ANALYSIS_SECTION_COLORS = {
    "section:Content Generation": (
        {"red": 0.86, "green": 0.92, "blue": 1.00},
        {"red": 0.11, "green": 0.31, "blue": 0.85},
    ),
    "section:Data Transfer": (
        {"red": 0.80, "green": 0.98, "blue": 0.94},
        {"red": 0.05, "green": 0.46, "blue": 0.41},
    ),
    "section:Memory Usage Report": (
        {"red": 1.00, "green": 0.95, "blue": 0.78},
        {"red": 0.71, "green": 0.32, "blue": 0.04},
    ),
    "section:Context Dependency Report": (
        {"red": 0.95, "green": 0.91, "blue": 1.00},
        {"red": 0.49, "green": 0.13, "blue": 0.81},
    ),
    "section:Buffer Cache Report": (
        {"red": 0.86, "green": 0.99, "blue": 0.91},
        {"red": 0.08, "green": 0.50, "blue": 0.24},
    ),
}
