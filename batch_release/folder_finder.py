"""Locating a batch's attachment folder by batch number + 'Lot created on' date."""
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# Date shapes we accept inside folder names (day-first, as used in Israel)
_DATE_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?<!\d)(\d{4})[.\-_](\d{1,2})[.\-_](\d{1,2})(?!\d)"), ["%Y-%m-%d"]),
    (re.compile(r"(?<!\d)(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{4})(?!\d)"), ["%d-%m-%Y"]),
    (re.compile(r"(?<!\d)(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{2})(?!\d)"), ["%d-%m-%y"]),
    (re.compile(r"(?<!\d)(\d{8})(?!\d)"), ["%Y%m%d", "%d%m%Y"]),
    (re.compile(r"(?<!\d)(\d{6})(?!\d)"), ["%d%m%y"]),
]


@dataclass
class FolderMatch:
    folder: Path | None
    files: list[Path] = field(default_factory=list)
    problem: str = ""
    candidates: list[Path] = field(default_factory=list)


# Every date that can be read out of a folder name
def dates_in_name(name: str) -> set[date]:
    found: set[date] = set()
    for pattern, formats in _DATE_PATTERNS:
        for m in pattern.finditer(name):
            text = "-".join(m.groups()) if len(m.groups()) > 1 else m.group(1)
            for fmt in formats:
                try:
                    found.add(datetime.strptime(text, fmt).date())
                except ValueError:
                    continue
    return found


# True when the batch appears in the name as a whole token (not glued to other letters/digits)
def name_contains_batch(name: str, batch: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(batch)}(?![A-Za-z0-9])"
    return re.search(pattern, name, flags=re.IGNORECASE) is not None


class FolderIndex:
    """One-time directory scan of the attachments root, reused for every row."""

    def __init__(self, root: Path, max_depth: int) -> None:
        self.root = root
        self.dirs: list[Path] = []
        if not root.is_dir():
            raise FileNotFoundError(f"attachments_root does not exist: {root}")
        base_depth = len(root.parts)
        for current, subdirs, _ in os.walk(root):
            depth = len(Path(current).parts) - base_depth
            if depth >= max_depth:
                subdirs[:] = []
            for d in subdirs:
                self.dirs.append(Path(current) / d)

    # Pick the folder for a batch; prefer the one whose name carries the lot-created date
    def find(self, batch: str, lot_created: date | None) -> FolderMatch:
        candidates = [d for d in self.dirs if name_contains_batch(d.name, batch)]
        # A folder nested inside another candidate is the same submission — keep the outermost
        candidates = [c for c in candidates if not any(o != c and o in c.parents for o in candidates)]
        if not candidates:
            return FolderMatch(None, problem=f"no folder containing batch '{batch}'")
        if len(candidates) == 1:
            return FolderMatch(candidates[0], candidates=candidates)
        if lot_created is None:
            return FolderMatch(None, problem="several folders and no 'Lot created on' date", candidates=candidates)
        dated = [c for c in candidates if lot_created in dates_in_name(c.name)]
        if len(dated) == 1:
            return FolderMatch(dated[0], candidates=candidates)
        reason = "none of them has" if not dated else f"{len(dated)} of them have"
        return FolderMatch(
            None,
            problem=f"{len(candidates)} folders match batch '{batch}', {reason} date {lot_created:%d.%m.%Y} — choose manually",
            candidates=candidates,
        )


MOH_SUB_DIR = "moh_sub"
REPLENISH_PREFIX = "ok 3rd p replenish"
REPORT_MARK = "report-"
COA_MARK = "coa"


@dataclass
class AttachmentChoice:
    files: list[Path]
    rule: str
    problem: str = ""


# Pick the files to upload, by the first rule that applies:
#   1. a MOH_SUB sub-folder        -> every file in it
#   2. 'OK 3rd P replenish*' file  -> those files + the file named exactly as the batch
#   3. a 'report-' file            -> those files + every 'COA' file + the file named exactly as the batch
#   otherwise the row is flagged for you (also when a rule's batch file or COA is missing)
def select_attachments(folder: Path, batch: str) -> AttachmentChoice:
    moh_sub = next((d for d in folder.iterdir() if d.is_dir() and d.name.lower() == MOH_SUB_DIR), None)
    if moh_sub is not None:
        files = _files_in(moh_sub)
        if not files:
            return AttachmentChoice([], "MOH_SUB", f"MOH_SUB folder is empty: {moh_sub}")
        return AttachmentChoice(files, "MOH_SUB")

    files = _files_in(folder)
    names = {p: _norm(p.name) for p in files}
    batch_files = [p for p in files if is_batch_file(p, batch)]
    no_batch_file = f"no file named exactly '{batch}' (e.g. {batch}.pdf)"

    if any(n.startswith(REPLENISH_PREFIX) for n in names.values()):
        chosen = _unique([p for p, n in names.items() if REPLENISH_PREFIX in n] + batch_files)
        return AttachmentChoice(chosen, "OK 3rd P replenish", "" if batch_files else no_batch_file)

    if any(REPORT_MARK in n for n in names.values()):
        coa = [p for p, n in names.items() if COA_MARK in n]
        chosen = _unique([p for p, n in names.items() if REPORT_MARK in n] + coa + batch_files)
        problem = "; ".join(m for m, missing in (("no COA file found", not coa), (no_batch_file, not batch_files))
                            if missing)
        return AttachmentChoice(chosen, "report- + COA + batch", problem)

    return AttachmentChoice([], "", "no MOH_SUB folder, 'OK 3rd P replenish' file or 'report-' file")


# A file whose name (without extension) is exactly the batch number, e.g. '44216.pdf'
def is_batch_file(path: Path, batch: str) -> bool:
    return _norm(path.stem) == batch.strip().lower()


# Keep order, drop repeats (a file can satisfy two conditions)
def _unique(paths: list[Path]) -> list[Path]:
    return list(dict.fromkeys(paths))


# Regular files directly in a folder, skipping Office lock files
def _files_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("~$"))


# Lower-case and collapse runs of spaces so 'OK  3rd P' still matches
def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()
