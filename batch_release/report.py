"""Results workbook and the ledger of requests already filed."""
import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

from .planner import Request

LEDGER_NAME = "filed_ledger.csv"
LEDGER_FIELDS = ["key", "batch", "license", "excel_file", "filed_at", "reference"]


# Keys of every request filed so far, so a re-run never files the same batch twice
def load_ledger(results_dir: Path) -> set[str]:
    path = results_dir / LEDGER_NAME
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["key"] for row in csv.DictReader(f)}


# Append one successfully filed request to the ledger
def append_ledger(results_dir: Path, req: Request, excel_file: str, reference: str) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / LEDGER_NAME
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({
            "key": req.key, "batch": req.batch, "license": req.license, "excel_file": excel_file,
            "filed_at": datetime.now().isoformat(timespec="seconds"), "reference": reference,
        })


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
        "Folder": str(r.folder or ""),
        "Files": "\n".join(p.name for p in r.files),
    } for r in plan]
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


# Count of requests per status, for the console summary
def summarize(plan: list[Request]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in plan:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
