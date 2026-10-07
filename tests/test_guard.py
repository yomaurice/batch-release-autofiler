from datetime import date

import pytest

from batch_release.config import UserProfile
from batch_release.planner import Request
from batch_release.portal import _norm_name, pick_greeting
from batch_release.runner import file_requests


def _req(user: str) -> Request:
    return Request(excel_row=2, batch="44216", product="X", license_raw="IL-1", license="1", mfg_date=date.today(),
                   expiry_date=date.today(), lot_created=date.today(), decided_by=user)


def test_runner_refuses_someone_elses_rows() -> None:
    gil = UserProfile("GSHETRIT", "Gil", "Gil", "גיל")
    with pytest.raises(ValueError, match="EBISMUTH"):
        file_requests(None, [_req("GSHETRIT"), _req("EBISMUTH")], "dry-run", "x.xlsx", None,  # type: ignore[arg-type]
                      profile=gil)


def test_greeting_match_ignores_spacing() -> None:
    assert _norm_name("דוד") in _norm_name("שלום,  דוד")
    assert _norm_name("גיל") not in _norm_name("שלום, דוד")


def test_bare_greeting_word_is_not_taken_as_the_name() -> None:
    # the portal splits it: <span>שלום</span><span>דוד</span> -> the texts around it are collected
    assert pick_greeting(["שלום", "שלום, ", "שלום דוד", "שלום דוד\nיציאה"]) == "שלום דוד"
    assert pick_greeting(["שלום"]) == ""
