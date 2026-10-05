"""Turning the SAP Excel export into a validated list of requests to file."""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Settings
from .folder_finder import FolderIndex, select_attachments
from .license import LicenseFormatError, to_portal_license

READY = "READY"
SKIPPED = "SKIPPED"
NEEDS_ATTENTION = "NEEDS_ATTENTION"


@dataclass
class Request:
    excel_row: int
    batch: str
    product: str
    license_raw: str
    license: str
    mfg_date: date | None
    expiry_date: date | None
    lot_created: date | None
    decided_by: str
    folder: Path | None = None
    files: list[Path] = field(default_factory=list)
    status: str = READY
    message: str = ""
    attach_rule: str = ""

    # Key used to recognise a request that was already filed in an earlier run
    @property
    def key(self) -> str:
        lot = self.lot_created.isoformat() if self.lot_created else ""
        return f"{self.batch}|{self.license}|{lot}"


# Read the export and resolve each row into a Request (nothing is sent anywhere here)
def build_plan(excel_path: Path, settings: Settings, already_filed: set[str]) -> list[Request]:
    cols = settings.columns
    df = pd.read_excel(excel_path, dtype=str).fillna("")
    missing = [c for c in cols.values() if c not in df.columns]
    if missing:
        raise ValueError(f"columns not found in {excel_path.name}: {missing}")

    indexes: dict[str, FolderIndex | str] = {}
    seen: dict[str, int] = {}
    plan: list[Request] = []

    for i, row in df.iterrows():
        batch = row[cols["batch"]].strip()
        decided_by = row[cols["decided_by"]].strip().upper()
        if not batch:  # totals row / blank lines at the bottom of the export
            continue
        if settings.users and decided_by not in settings.users:
            continue

        req = Request(
            excel_row=int(i) + 2,
            batch=batch,
            product=row[cols["product"]].strip(),
            license_raw=row[cols["license"]].strip(),
            license="",
            mfg_date=_as_date(row[cols["mfg_date"]]),
            expiry_date=_as_date(row[cols["expiry_date"]]),
            lot_created=_as_date(row[cols["lot_created"]]),
            decided_by=decided_by,
        )
        plan.append(req)
        _validate(req, _index_for(decided_by, settings, indexes), seen, already_filed)
    return plan


# Fill in license/folder/files and mark the row READY, SKIPPED or NEEDS_ATTENTION
def _validate(req: Request, index: FolderIndex | str,
              seen: dict[str, int], already_filed: set[str]) -> None:
    try:
        req.license = to_portal_license(req.license_raw)
    except LicenseFormatError as exc:
        req.status, req.message = SKIPPED, str(exc)
        return
    if req.mfg_date is None or req.expiry_date is None:
        req.status, req.message = NEEDS_ATTENTION, "missing MFG or expiry date"
        return
    if req.key in already_filed:
        req.status, req.message = SKIPPED, "already filed in a previous run"
        return
    if req.key in seen:
        req.status, req.message = SKIPPED, f"duplicate of Excel row {seen[req.key]}"
        return
    seen[req.key] = req.excel_row

    if isinstance(index, str):  # the user's folder could not be scanned
        req.status, req.message = NEEDS_ATTENTION, index
        return
    match = index.find(req.batch, req.lot_created)
    if match.folder is None:
        listed = "; ".join(str(c) for c in match.candidates)
        req.status = NEEDS_ATTENTION
        req.message = match.problem + (f" [{listed}]" if listed else "")
        return
    req.folder = match.folder
    choice = select_attachments(match.folder, req.batch)
    req.files, req.attach_rule = choice.files, choice.rule
    if choice.problem:
        req.status, req.message = NEEDS_ATTENTION, choice.problem


# Scan each user's personal folder once (root/<folder name>); a scan error is kept as a message
def _index_for(user: str, settings: Settings, cache: dict[str, "FolderIndex | str"]) -> "FolderIndex | str":
    if user not in cache:
        sub = settings.users.get(user)
        root = settings.attachments_root / sub if sub else settings.attachments_root
        try:
            cache[user] = FolderIndex(root, settings.folder_search_depth)
        except FileNotFoundError:
            cache[user] = f"folder for user {user} not found: {root}"
    return cache[user]


# Excel cells arrive as text like '2026-04-15 00:00:00'; anything unparsable becomes None
def _as_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()
