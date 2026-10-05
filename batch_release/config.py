"""Loading of config.yaml into a typed settings object."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COLUMNS = {
    "batch": "Batch",
    "mfg_date": "MFG Date",
    "expiry_date": "Expiry Date",
    "license": "GI External License number",
    "lot_created": "Lot created on",
    "decided_by": "Usage dec. made by",
    "product": "Short text for inspection object",
}


@dataclass
class Settings:
    portal_url: str
    attachments_root: Path
    users: list[str]
    columns: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLUMNS))
    folder_search_depth: int = 4
    attach_extensions: list[str] = field(default_factory=lambda: [".pdf"])
    attach_recursive: bool = False
    portal_date_format: str = "%d/%m/%Y"
    browser_channel: str = "chrome"
    browser_profile_dir: Path = PROJECT_ROOT / "browser_profile"
    results_dir: Path = PROJECT_ROOT / "results"
    selectors: dict[str, str] = field(default_factory=dict)


# Read config.yaml (relative paths resolve against the project root)
def load_settings(path: Path) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    columns = {**DEFAULT_COLUMNS, **(raw.get("columns") or {})}
    return Settings(
        portal_url=raw.get("portal_url", "https://qpbatchrelease.health.gov.il/batch-release"),
        attachments_root=_resolve(raw["attachments_root"]),
        users=[str(u).strip().upper() for u in raw.get("users") or []],
        columns=columns,
        folder_search_depth=int(raw.get("folder_search_depth", 4)),
        attach_extensions=[e.lower() for e in raw.get("attach_extensions", [".pdf"])],
        attach_recursive=bool(raw.get("attach_recursive", False)),
        portal_date_format=raw.get("portal_date_format", "%d/%m/%Y"),
        browser_channel=raw.get("browser_channel", "chrome"),
        browser_profile_dir=_resolve(raw.get("browser_profile_dir", "browser_profile")),
        results_dir=_resolve(raw.get("results_dir", "results")),
        selectors=raw.get("selectors") or {},
    )


# Absolute paths stay as-is; relative ones are anchored at the project root
def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p
