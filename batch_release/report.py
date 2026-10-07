"""Results workbook, and the record of every request submitted (used to never file the same one twice)."""
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from .planner import Request

# Every submission is appended as one JSON line. Nobody opens this file, so it can't be locked by Excel:
# it is the source of truth. The Excel log below is rebuilt from it after every submission.
JOURNAL_NAME = "submissions.jsonl"
LOG_NAME = "Submitted requests.xlsx"
LOG_COPY_NAME = "Submitted requests - updated copy.xlsx"  # written when the log is open in Excel
LEGACY_LEDGER_NAME = "filed_ledger.csv"                   # older runs, still honoured for duplicates

LOG_COLUMNS = {
    "submitted_at": "Submitted at", "reference": "MOH request number", "batch": "Batch",
    "product": "Product", "license": "License", "mfg_date": "MFG date", "expiry_date": "Expiry date",
    "lot_created": "Lot created on", "filed_by": "Filed by", "excel_file": "SAP export",
    "files": "Files uploaded", "screenshot": "Confirmation screenshot", "folders": "Batch folder(s)",
}


# Keys of every request filed so far, so a re-run never files the same batch twice
def load_ledger(results_dir: Path) -> set[str]:
    keys = {e["key"] for e in _read_journal(results_dir)}
    legacy = results_dir / LEGACY_LEDGER_NAME
    if legacy.exists():
        with legacy.open(encoding="utf-8-sig", newline="") as f:
            keys |= {row["key"] for row in csv.DictReader(f)}
    return keys


# Record one submitted request, then refresh the Excel log. Returns a note when the log could not be
# updated (open in Excel) — the submission itself is always recorded.
def record_submission(results_dir: Path, req: Request, excel_file: str, reference: str,
                      screenshot: Path | None, filed_by: str) -> str:
    results_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "key": req.key, "submitted_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "reference": reference, "batch": req.batch, "product": req.product, "license": req.license,
        "mfg_date": _iso(req.mfg_date), "expiry_date": _iso(req.expiry_date), "lot_created": _iso(req.lot_created),
        "filed_by": filed_by, "excel_file": excel_file, "files": [p.name for p in req.files],
        "screenshot": str(screenshot or ""), "folders": [str(f) for f in req.folders],
    }
    with (results_dir / JOURNAL_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return refresh_submissions_log(results_dir)


# Rebuild 'Submitted requests.xlsx' from the journal. If it is open in Excel, retry for a few seconds,
# then write an up-to-date copy next to it; the main file catches up on the next refresh after it is closed.
def refresh_submissions_log(results_dir: Path, retries: int = 5) -> str:
    entries = _read_journal(results_dir)
    if not entries:
        return ""
    target = results_dir / LOG_NAME
    tmp = results_dir / f"~tmp {os.getpid()} {LOG_NAME}"
    _write_log(entries, tmp)
    for attempt in range(retries):
        try:
            os.replace(tmp, target)
            _remove_quietly(results_dir / LOG_COPY_NAME)
            return ""
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(1.0)
    try:
        os.replace(tmp, results_dir / LOG_COPY_NAME)
        return f"'{LOG_NAME}' is open, so the latest log was saved as '{LOG_COPY_NAME}'"
    except PermissionError:
        _remove_quietly(tmp)
        return f"'{LOG_NAME}' is open; it will be updated once it is closed"


def submissions_log_path(results_dir: Path) -> Path:
    return results_dir / LOG_NAME


def _write_log(entries: list[dict], path: Path) -> None:
    rows = [{title: ("\n".join(e.get(k) or []) if k in ("files", "folders") else e.get(k, ""))
             for k, title in LOG_COLUMNS.items()} for e in reversed(entries)]  # newest first
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        pd.DataFrame(rows, columns=list(LOG_COLUMNS.values())).to_excel(xl, index=False, sheet_name="Submitted")
        sheet = xl.sheets["Submitted"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for col, title in zip(sheet.columns, LOG_COLUMNS.values()):
            sheet.column_dimensions[col[0].column_letter].width = 45 if title in (
                "Files uploaded", "Confirmation screenshot", "Batch folder(s)") else max(14, len(title) + 2)


def _read_journal(results_dir: Path) -> list[dict]:
    path = results_dir / JOURNAL_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _iso(value: object) -> str:
    return value.isoformat() if value else ""


# Write the plan/results table as an Excel file and return its path
def write_report(results_dir: Path, plan: list[Request], label: str) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{label}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    rows = [{
        "Excel row": r.excel_row,
        "Status": r.status,
        "Message": r.message,
        "Batch": r.batch,
        "Product": r.product,
        "License (SAP)": r.license_raw,
        "License (portal)": r.license,
        "MFG date": r.mfg_date,
        "Expiry date": r.expiry_date,
        "Lot created on": r.lot_created,
        "Decided by": r.decided_by,
        "UD code": r.ud_code,
        "Folder": "\n".join(map(str, r.folders)),
        "Attach rule": r.attach_rule,
        "Files":"\n".join(p.name for p in r.files),
    } for r in plan]
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


# Count of requests per status, for the console summary
def summarize(plan: list[Request]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in plan:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
