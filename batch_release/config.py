"""Loading of config.yaml into a typed settings object."""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Where config.yaml and results\ live. In the packaged app (BatchRelease.exe) that is Documents\BatchRelease,
# so replacing the app folder with a newer build keeps your settings and records. BUNDLE_ROOT holds the
# files shipped with the app (config.example.yaml).
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path.home() / "Documents" / "BatchRelease"
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    PROJECT_ROOT = BUNDLE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COLUMNS = {
    "batch": "Batch",
    "mfg_date": "MFG Date",
    "expiry_date": "Expiry Date",
    "license": "GI External License number",
    "lot_created": "Lot created on",
    "decided_by": "Usage dec. made by",
    "product": "Short text for inspection object",
    "ud_code": "UD code",
}

MODES = ("dry-run", "confirm", "auto")


@dataclass
class UserProfile:
    """One person who files requests: their SAP name, folder on the share and identity on the portal."""
    sap_user: str
    folder: str | None = None        # sub-folder under attachments_root (None = search the whole root)
    display_name: str = ""           # shown in the app, e.g. 'Dudi'
    portal_name: str = ""            # name in the portal's 'שלום, <name>' greeting, used to verify the login

    @property
    def label(self) -> str:
        return f"{self.display_name or self.folder or self.sap_user} ({self.sap_user})"


@dataclass
class Settings:
    portal_url: str
    attachments_root: Path
    users: dict[str, UserProfile]
    columns: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLUMNS))
    folder_search_depth: int = 4
    portal_date_format: str = "%d/%m/%Y"
    browser_channel: str = "chrome"
    results_dir: Path = PROJECT_ROOT / "results"
    selectors: dict[str, str] = field(default_factory=dict)
    modes: tuple[str, ...] = MODES   # modes offered in the app (e.g. only 'auto' once things are stable)
    default_mode: str = "dry-run"    # mode selected when the app opens; always one of `modes`


# Read config.yaml (relative paths resolve against the project root)
def load_settings(path: Path) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    columns = {**DEFAULT_COLUMNS, **(raw.get("columns") or {})}
    return Settings(
        portal_url=raw.get("portal_url", "https://qpbatchrelease.health.gov.il/batch-release"),
        attachments_root=_resolve(raw["attachments_root"]),
        users=_parse_users(raw.get("users")),
        columns=columns,
        folder_search_depth=int(raw.get("folder_search_depth", 4)),
        portal_date_format=raw.get("portal_date_format", "%d/%m/%Y"),
        browser_channel=raw.get("browser_channel", "chrome"),
        results_dir=_resolve(raw.get("results_dir", "results")),
        selectors=raw.get("selectors") or {},
        **_modes(raw),
    )


# 'modes' = the modes offered (in the standard order); 'default_mode' falls back to the first offered one
def _modes(raw: dict) -> dict:
    offered = [_mode(m, "modes") for m in (raw.get("modes") or MODES)]
    modes = tuple(m for m in MODES if m in offered)
    default = _mode(raw.get("default_mode") or modes[0], "default_mode")
    return {"modes": modes, "default_mode": default if default in modes else modes[0]}


def _mode(value: object, key: str) -> str:
    mode = str(value).strip().lower()
    if mode not in MODES:
        raise ValueError(f"{key}: '{value}' is not a mode — use {', '.join(MODES)}")
    return mode


# Absolute paths stay as-is; relative ones are anchored at the project root
def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


# 'users' maps SAP name -> folder name (short form) or -> {folder, display_name, portal_name}
def _parse_users(value: object) -> dict[str, UserProfile]:
    if not value:
        return {}
    if not isinstance(value, dict):
        return {str(u).strip().upper(): UserProfile(str(u).strip().upper()) for u in value}  # type: ignore[union-attr]
    users: dict[str, UserProfile] = {}
    for key, entry in value.items():
        sap = str(key).strip().upper()
        if isinstance(entry, dict):
            folder = str(entry.get("folder") or "").strip() or None
            users[sap] = UserProfile(sap, folder, str(entry.get("display_name") or folder or "").strip(),
                                     str(entry.get("portal_name") or "").strip())
        else:
            folder = str(entry).strip() if entry else None
            users[sap] = UserProfile(sap, folder, folder or "")
    return users
