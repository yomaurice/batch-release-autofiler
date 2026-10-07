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
    folders: list[Path] = field(default_factory=list)
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

    # Folders for a batch. Several matches are combined into one set of files, except folders
    # whose name carries a different date than 'Lot created on' (those belong to another lot)
    def find(self, batch: str, lot_created: date | None) -> FolderMatch:
        candidates = [d for d in self.dirs if name_contains_batch(d.name, batch)]
        # A folder nested inside another candidate is the same submission — keep the outermost
        candidates = [c for c in candidates if not any(o != c and o in c.parents for o in candidates)]
        if not candidates:
            return FolderMatch(problem=f"no folder containing batch '{batch}'")
        if len(candidates) == 1 or lot_created is None:
            return FolderMatch(candidates, candidates=candidates)
        same_lot = [c for c in candidates if lot_created in dates_in_name(c.name) or not dates_in_name(c.name)]
        if not same_lot:
            return FolderMatch(
                problem=f"{len(candidates)} folders match batch '{batch}', none has date {lot_created:%d.%m.%Y}"
                        " — choose manually",
                candidates=candidates,
            )
        return FolderMatch(same_lot, candidates=candidates)


MOH_SUB_DIR = "moh_sub"
REPLENISH_PREFIX = "ok 3rd p replenish"
REPORT_MARK = "report-"
DATA_LOGGER_MARK = "data logger"
COA_MARK = "coa"


@dataclass
class AttachmentChoice:
    files: list[Path]
    rule: str
    problem: str = ""


# Pick the files to upload from the batch's folder(s), treated as one combined folder.
# The first rule that applies wins:
#   1. a MOH_SUB sub-folder              -> every file in it (nothing outside it)
#   2. 'OK 3rd P replenish*' file        -> those data logger files + the latest file named exactly as the batch
#   3. a 'report-' file                  -> those data logger files + the latest 'COA' file
#   4. a 'data logger' file              -> those data logger files + the latest 'COA' file
#   otherwise the row is flagged for you (also when the rule's COA / batch file is missing)
# "Latest" = most recently modified.
def select_attachments(folders: list[Path], batch: str) -> AttachmentChoice:
    moh_subs = [d for f in folders for d in f.iterdir() if d.is_dir() and d.name.lower() == MOH_SUB_DIR]
    if moh_subs:
        files = _unique([p for d in moh_subs for p in _files_in(d)])
        if not files:
            return AttachmentChoice([], "MOH_SUB", "MOH_SUB folder is empty: " + "; ".join(map(str, moh_subs)))
        return AttachmentChoice(files, "MOH_SUB")

    files = _unique([p for f in folders for p in _files_in(f)])
    names = {p: _norm(p.name) for p in files}

    if any(n.startswith(REPLENISH_PREFIX) for n in names.values()):
        loggers = [p for p, n in names.items() if REPLENISH_PREFIX in n]
        coa = _latest([p for p in files if is_batch_file(p, batch)])
        problem = "" if coa else f"no file named exactly '{batch}' (e.g. {batch}.pdf)"
        return AttachmentChoice(loggers + coa, "OK 3rd P replenish + latest batch file", problem)

    for mark, rule in ((REPORT_MARK, "report- + latest COA"), (DATA_LOGGER_MARK, "data logger + latest COA")):
        loggers = [p for p, n in names.items() if mark in n]
        if loggers:
            coa = _latest([p for p, n in names.items() if COA_MARK in n and p not in loggers])
            return AttachmentChoice(loggers + coa, rule, "" if coa else "no COA file found")

    return AttachmentChoice([], "", "no MOH_SUB folder, 'OK 3rd P replenish', 'report-' or 'data logger' file")


# The most recently modified file, as a 0- or 1-item list
def _latest(paths: list[Path]) -> list[Path]:
    return [max(paths, key=lambda p: p.stat().st_mtime)] if paths else []


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
