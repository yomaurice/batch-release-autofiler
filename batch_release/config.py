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
    # SAP user -> name of that user's sub-folder under attachments_root (None = search the whole root)
    users: dict[str, str | None]
    columns: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLUMNS))
    folder_search_depth: int = 4
    portal_date_format: str = "%d/%m/%Y"
    browser_channel: str = "chrome"
    results_dir: Path = PROJECT_ROOT / "results"
    selectors: dict[str, str] = field(default_factory=dict)


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
    )


# Absolute paths stay as-is; relative ones are anchored at the project root
def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


# 'users' may be a list of SAP names or a mapping SAP name -> personal folder name
def _parse_users(value: object) -> dict[str, str | None]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k).strip().upper(): (str(v).strip() if v else None) for k, v in value.items()}
    return {str(u).strip().upper(): None for u in value}  # type: ignore[union-attr]
