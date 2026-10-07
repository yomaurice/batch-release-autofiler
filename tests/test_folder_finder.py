import os
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
    assert [f.name for f in m.folders] == ["ABC123 15.04.2026"]


def test_date_picks_between_several(tmp_path: Path) -> None:
    _make(tmp_path, "2025/ABC123 01.01.2025", "2026/ABC123 15.04.2026")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert [f.name for f in m.folders] == ["ABC123 15.04.2026"]


def test_several_same_lot_folders_are_combined(tmp_path: Path) -> None:
    _make(tmp_path, "a/ABC123 15.04.2026", "b/ABC123 15.04.2026 copy", "c/ABC123 data logger", "d/ABC123 01.01.2025")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert sorted(f.name for f in m.folders) == ["ABC123 15.04.2026", "ABC123 15.04.2026 copy", "ABC123 data logger"]
    assert not m.problem


def test_only_other_lot_dates_is_reported(tmp_path: Path) -> None:
    _make(tmp_path, "a/ABC123 01.01.2025", "b/ABC123 02.02.2025")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert m.folders == [] and "choose manually" in m.problem and len(m.candidates) == 2


def test_nested_match_counts_once(tmp_path: Path) -> None:
    _make(tmp_path, "ABC123 15.04.2026/ABC123 COA")
    m = FolderIndex(tmp_path, 4).find("ABC123", date(2026, 4, 15))
    assert [f.name for f in m.folders] == ["ABC123 15.04.2026"]


def test_missing_folder(tmp_path: Path) -> None:
    m = FolderIndex(tmp_path, 4).find("ZZZ", None)
    assert m.folders == [] and "no folder" in m.problem


def _files(folder: Path, *names: str) -> None:
    for n in names:
        p = folder / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


# Give files increasing modification times, oldest first
def _age(folder: Path, *names: str) -> None:
    for i, n in enumerate(names):
        os.utime(folder / n, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))


def _names(c) -> list[str]:
    return sorted(p.name for p in c.files)


def test_moh_sub_wins(tmp_path: Path) -> None:
    _files(tmp_path, "MOH_SUB/a.pdf", "MOH_SUB/b.pdf", "OK 3rd P replenish.pdf", "report-1.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.rule == "MOH_SUB" and _names(c) == ["a.pdf", "b.pdf"] and not c.problem


def test_replenish_plus_latest_batch_file(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _files(a, "OK 3rd P replenish 1.pdf", "44216.pdf", "COA 44216.pdf", "report-x.pdf")
    _files(b, "x OK 3rd P replenish 2.pdf", "44216.pdf", "44216 old.pdf")
    os.utime(a / "44216.pdf", (1_700_000_000, 1_700_000_000))
    os.utime(b / "44216.pdf", (1_800_000_000, 1_800_000_000))
    c = select_attachments([a, b], "44216")
    assert c.rule.startswith("OK 3rd P replenish") and not c.problem
    assert _names(c) == ["44216.pdf", "OK 3rd P replenish 1.pdf", "x OK 3rd P replenish 2.pdf"]
    assert b / "44216.pdf" in c.files and a / "44216.pdf" not in c.files


def test_replenish_without_batch_file_is_flagged(tmp_path: Path) -> None:
    _files(tmp_path, "OK 3rd P replenish 1.pdf", "COA 44216.pdf")
    assert "no file named exactly" in select_attachments([tmp_path], "44216").problem


def test_report_plus_latest_coa(tmp_path: Path) -> None:
    _files(tmp_path, "report-44216.pdf", "report-44216 b.pdf", "COA old.pdf", "COA new.pdf", "44216.pdf", "checklist.pdf")
    _age(tmp_path, "COA new.pdf", "COA old.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.rule == "report- + latest COA" and not c.problem
    assert _names(c) == ["COA old.pdf", "report-44216 b.pdf", "report-44216.pdf"]


def test_report_without_coa_is_flagged(tmp_path: Path) -> None:
    _files(tmp_path, "report-44216.pdf", "44216.pdf")
    assert "no COA" in select_attachments([tmp_path], "44216").problem


def test_data_logger_plus_latest_coa(tmp_path: Path) -> None:
    _files(tmp_path, "DATA LOGGER 1.pdf", "x Data  Logger 2.csv", "coa 44216.pdf", "COA 44216 v2.pdf", "44216.pdf")
    _age(tmp_path, "coa 44216.pdf", "COA 44216 v2.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.rule == "data logger + latest COA" and not c.problem
    assert _names(c) == ["COA 44216 v2.pdf", "DATA LOGGER 1.pdf", "x Data  Logger 2.csv"]


def test_report_beats_data_logger(tmp_path: Path) -> None:
    _files(tmp_path, "report-1.pdf", "data logger.pdf", "COA.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.rule == "report- + latest COA" and _names(c) == ["COA.pdf", "report-1.pdf"]


def test_combined_folders_moh_sub_in_one(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _files(a, "report-1.pdf", "COA.pdf")
    _files(b, "MOH_SUB/pkg.pdf")
    c = select_attachments([a, b], "44216")
    assert c.rule == "MOH_SUB" and _names(c) == ["pkg.pdf"]


def test_combined_folders_logger_and_coa_split(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _files(a, "data logger 44216.pdf")
    _files(b, "COA 44216.pdf", "notes.pdf")
    c = select_attachments([a, b], "44216")
    assert not c.problem and _names(c) == ["COA 44216.pdf", "data logger 44216.pdf"]


def test_coa_alone_is_not_uploaded(tmp_path: Path) -> None:
    _files(tmp_path, "COA 44216.pdf", "44216.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.files == [] and c.problem


def test_nothing_matches_is_flagged(tmp_path: Path) -> None:
    _files(tmp_path, "checklist.pdf")
    c = select_attachments([tmp_path], "44216")
    assert c.files == [] and c.problem
