from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from batch_release.config import Settings, UserProfile, load_settings
from batch_release.planner import SKIPPED, Request, build_plan
from batch_release.report import (LOG_COPY_NAME, LOG_NAME, load_ledger, record_submission,
                                  refresh_submissions_log)


def _req(batch: str) -> Request:
    return Request(excel_row=2, batch=batch, product="P", license_raw="IL-1.2.3.4", license="1-2-3-4",
                   mfg_date=date(2026, 1, 1), expiry_date=date(2028, 1, 1), lot_created=date(2026, 5, 1),
                   decided_by="U1", ud_code="A")


def _log(results: Path) -> pd.DataFrame:
    return pd.read_excel(results / LOG_NAME, dtype=str)


def test_every_submission_is_logged_newest_first(tmp_path: Path) -> None:
    for batch, ref in (("111", "9001"), ("222", "9002")):
        assert record_submission(tmp_path, _req(batch), "exp.xlsx", ref, tmp_path / "shot.png", "Dudi") == ""
    log = _log(tmp_path)
    assert list(log["Batch"]) == ["222", "111"] and list(log["MOH request number"]) == ["9002", "9001"]
    assert load_ledger(tmp_path) == {_req("111").key, _req("222").key}


def test_log_open_elsewhere_loses_nothing(tmp_path: Path) -> None:
    record_submission(tmp_path, _req("111"), "exp.xlsx", "9001", None, "Dudi")
    with open(tmp_path / LOG_NAME, "rb"):  # held open, like Excel does
        note = record_submission(tmp_path, _req("222"), "exp.xlsx", "9002", None, "Dudi")
        assert "open" in note
        assert list(pd.read_excel(tmp_path / LOG_COPY_NAME, dtype=str)["Batch"]) == ["222", "111"]
    assert _req("222").key in load_ledger(tmp_path)  # duplicate guard still knows about it
    refresh_submissions_log(tmp_path)                 # once closed, the main log catches up
    assert list(_log(tmp_path)["Batch"]) == ["222", "111"] and not (tmp_path / LOG_COPY_NAME).exists()


def test_empty_ud_code_is_skipped(tmp_path: Path) -> None:
    pd.DataFrame({
        "Batch": ["111", "222"], "MFG Date": ["2026-01-01"] * 2, "Expiry Date": ["2028-01-01"] * 2,
        "GI External License number": ["IL-146.13.33189.00"] * 2, "Lot created on": ["2026-05-01"] * 2,
        "Usage dec. made by": ["U1"] * 2, "Short text for inspection object": ["P"] * 2, "UD code": ["A", ""],
    }).to_excel(tmp_path / "exp.xlsx", index=False)
    (tmp_path / "root" / "u1").mkdir(parents=True)
    settings = Settings("x", tmp_path / "root", {"U1": UserProfile("U1", "u1")})
    plan = build_plan(tmp_path / "exp.xlsx", settings, set())
    assert plan[1].status == SKIPPED and plan[1].message == "UD code is empty"
    assert plan[0].status != SKIPPED


def test_default_mode_setting(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("attachments_root: x\ndefault_mode: auto\n", encoding="utf-8")
    assert load_settings(cfg).default_mode == "auto"
    cfg.write_text("attachments_root: x\ndefault_mode: always\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg)
