from pathlib import Path

import pytest

from batch_release.config import load_settings
from batch_release.config_store import save_changes
from batch_release.portal import SITE_ERROR_RE

BASE = """# top comment
attachments_root: x   # where batches live
users:
  DSABAG01:
    folder: Dudi
    portal_name: דוד      # checked after login
  GSHETRIT:
    folder: Gil
columns:
  batch: Batch
"""


def test_modes_offered_and_default(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("attachments_root: x\nmodes: [auto, dry-run]\ndefault_mode: confirm\n", encoding="utf-8")
    s = load_settings(cfg)
    assert s.modes == ("dry-run", "auto") and s.default_mode == "dry-run"  # confirm not offered -> first offered
    cfg.write_text("attachments_root: x\n", encoding="utf-8")
    assert load_settings(cfg).modes == ("dry-run", "confirm", "auto")
    cfg.write_text("attachments_root: x\nmodes: [sometimes]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg)


def test_save_keeps_comments_and_applies_changes(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(BASE, encoding="utf-8")
    save_changes(cfg, {
        "modes": ["auto"], "default_mode": "auto", "attachments_root": r"S:\Batch ready",
        "users": {"DSABAG01": {"display_name": "Dudi", "folder": "Dudi", "portal_name": "דוד"},
                  "YM": {"display_name": "Yoni", "folder": "Yoni", "portal_name": "יונתן"}},
        "columns": {"ud_code": "UD Code"},
    })
    text = cfg.read_text(encoding="utf-8")
    assert "# top comment" in text and "# checked after login" in text and "modes: [auto]" in text
    s = load_settings(cfg)
    assert s.modes == ("auto",) and str(s.attachments_root) == r"S:\Batch ready"
    assert list(s.users) == ["DSABAG01", "YM"]                      # Gil removed, Yoni added
    assert s.columns["batch"] == "Batch" and s.columns["ud_code"] == "UD Code"


def test_site_error_words() -> None:
    assert SITE_ERROR_RE.search("שגיאה אירעה שגיאה במהלך שליפת נתוני התכשיר אישור")
    assert not SITE_ERROR_RE.search("הבקשה נשלחה בהצלחה, מספר בקשה 123456")
