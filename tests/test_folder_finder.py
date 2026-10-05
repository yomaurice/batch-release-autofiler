from datetime import date
from pathlib import Path

from batch_release.folder_finder import FolderIndex, dates_in_name, name_contains_batch, select_attachments


def _make(root: Path, *names: str) -> None:
    for n in names:
        (root / n).mkdir(parents=True)


def test_dates_in_name_formats() -> None:
    d = date(2026, 4, 15)
    for name in ("X 15.04.2026", "X 15-04-26", "X_2026-04-15", "X 20260415", "X 150426"):
        assert d in dates_in_name(name), name


def test_batch_must_be_whole_token() -> None:
    assert name_contains_batch("017614 15.04.2026", "017614")
    assert not name_contains_batch("0176145 15.04.2026", "017614")
    assert name_contains_batch("hb8726001b", "HB8726001B")


def test_single_folder_is_used(tmp_path: Path) -> None:
    _make(tmp_path, "2026/ABC123 15.04.2026")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert m.folder and m.folder.name == "ABC123 15.04.2026"


def test_date_picks_between_several(tmp_path: Path) -> None:
    _make(tmp_path, "2025/ABC123 01.01.2025", "2026/ABC123 15.04.2026")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert m.folder and m.folder.name == "ABC123 15.04.2026"


def test_ambiguous_is_reported(tmp_path: Path) -> None:
    _make(tmp_path, "a/ABC123 15.04.2026", "b/ABC123 15.04.2026 copy")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert m.folder is None and "choose manually" in m.problem and len(m.candidates) == 2


def test_nested_match_counts_once(tmp_path: Path) -> None:
    _make(tmp_path, "ABC123 15.04.2026/ABC123 COA")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert m.folder and m.folder.name == "ABC123 15.04.2026"


def test_missing_folder(tmp_path: Path) -> None:
    m = FolderIndex(tmp_path, 4).find("ZZZ", None)
    assert m.folder is None and "no folder" in m.problem


def _files(folder: Path, *names: str) -> None:
    for n in names:
        p = folder / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_moh_sub_wins(tmp_path: Path) -> None:
    _files(tmp_path, "MOH_SUB/a.pdf", "MOH_SUB/b.pdf", "OK 3rd P replenish.pdf", "report-1.pdf")
    c = select_attachments(tmp_path, "44216")
    assert c.rule == "MOH_SUB" and sorted(p.name for p in c.files) == ["a.pdf", "b.pdf"] and not c.problem


def test_replenish_plus_batch_file(tmp_path: Path) -> None:
    _files(tmp_path, "OK 3rd P replenish 1.pdf", "x OK 3rd P replenish 2.pdf", "44216.pdf", "COA.pdf", "report-x.pdf")
    c = select_attachments(tmp_path, "44216")
    assert c.rule == "OK 3rd P replenish" and not c.problem
    assert sorted(p.name for p in c.files) == ["44216.pdf", "OK 3rd P replenish 1.pdf", "x OK 3rd P replenish 2.pdf"]


def test_replenish_without_batch_file_is_flagged(tmp_path: Path) -> None:
    _files(tmp_path, "OK 3rd P replenish 1.pdf")
    assert "no file named after batch" in select_attachments(tmp_path, "44216").problem


def test_report_plus_coa(tmp_path: Path) -> None:
    _files(tmp_path, "report-44216.pdf", "COA 44216.pdf", "checklist.pdf")
    c = select_attachments(tmp_path, "44216")
    assert c.rule == "report- + COA" and sorted(p.name for p in c.files) == ["COA 44216.pdf", "report-44216.pdf"]


def test_nothing_matches_is_flagged(tmp_path: Path) -> None:
    _files(tmp_path, "checklist.pdf")
    c = select_attachments(tmp_path, "44216")
    assert c.files == [] and c.problem
