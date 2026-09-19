from __future__ import annotations

import random
import time

import gspread
from google.oauth2.service_account import Credentials

from .constants import REQUIRED_SPREADSHEET_LOCALE


def google_client(service_account_path: str):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(service_account_path, scopes=scopes)
    return gspread.authorize(creds)


def google_api_call(action, *args, **kwargs):
    """Retry with exponential backoff when Google rate-limits requests."""
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(8):
        try:
            return action(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status not in retryable_statuses or attempt == 7:
                raise
            retry_after = None
            headers = getattr(response, "headers", None) or {}
            try:
                retry_after = float(headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            base_delay = retry_after if retry_after is not None else 2 ** attempt
            delay = min(base_delay, 60) + random.uniform(0.1, 0.8)
            print(
                f"[WAIT] Google API rate limit ({status}); "
                f"retrying in {delay:.1f}s..."
            )
            time.sleep(delay)


def ensure_spreadsheet_locale(book):
    """Enforce the locale required by formulas and boolean controls."""
    locale = REQUIRED_SPREADSHEET_LOCALE
    properties = getattr(book, "_properties", {}) or {}
    current_locale = str(
        properties.get("locale") or getattr(book, "locale", "") or ""
    ).replace("-", "_")
    if current_locale.casefold() == locale.casefold():
        return
    google_api_call(book.batch_update, {"requests": [{
        "updateSpreadsheetProperties": {
            "properties": {"locale": locale},
            "fields": "locale",
        }
    }]})
    if isinstance(properties, dict):
        properties["locale"] = locale
    print(f"[CONFIG] Spreadsheet locale set to {locale}.")


def ensure_worksheet(book, title: str, rows=2000, cols=200):
    try:
        return google_api_call(book.worksheet, title)
    except gspread.WorksheetNotFound:
        return google_api_call(book.add_worksheet, title=title, rows=rows, cols=cols)


def _extended_value(value: object) -> dict:
    """Convert a Python scalar to an ExtendedValue for batch updates."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, (int, float)):
        return {"numberValue": value}
    if isinstance(value, str) and value.startswith("=HYPERLINK("):
        return {"formulaValue": value}
    return {"stringValue": "" if value is None else str(value)}
