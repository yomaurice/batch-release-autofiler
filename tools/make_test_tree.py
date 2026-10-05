"""Build a fake attachments tree from an SAP export, mirroring the S: drive, for testing off-site."""
import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from batch_release.config import PROJECT_ROOT, load_settings  # noqa: E402

# Smallest valid PDF, so the portal accepts the upload
_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
MANIFEST_NAME = "_test_tree_manifest.csv"
_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')

# File sets per scenario; rows cycle through them so every attachment rule is exercised
_SCENARIOS: list[tuple[str, list[str]]] = [
    ("moh_sub", ["MOH_SUB/MOH package 1.pdf", "MOH_SUB/MOH package 2.pdf", "COA {batch}.pdf", "internal notes.pdf"]),
    ("replenish", ["OK 3rd P replenish {batch}.pdf", "OK 3rd P replenish annex.pdf", "{batch}.pdf", "other.pdf"]),
    ("report", ["report-{batch}.pdf", "COA {batch}.pdf", "{batch}.pdf", "{batch} draft.pdf", "checklist.pdf"]),
    ("nothing", ["checklist.pdf", "scan001.pdf"]),
]


# <root>/<user folder>/<PRODUCT> <batch> <dd.mm.yyyy> <delivery>/...  — same shape as the real share
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", type=Path)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "test_data" / "s_drive")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--limit", type=int, default=16)
    args = parser.parse_args()

    settings = load_settings(args.config)
    cols = settings.columns
    df = pd.read_excel(args.excel, dtype=str).fillna("")
    df = df[df[cols["batch"]].str.strip() != ""]
    if settings.users:
        df = df[df[cols["decided_by"]].str.strip().str.upper().isin(settings.users)]
    df = df.drop_duplicates([cols["batch"], cols["lot_created"]]).head(args.limit)

    made = 0
    created: list[dict[str, str]] = []
    for _, row in df.iterrows():
        batch = row[cols["batch"]].strip()
        if _BAD_CHARS.search(batch):
            continue
        user = row[cols["decided_by"]].strip().upper()
        lot = pd.to_datetime(row[cols["lot_created"]]).date()
        product = _BAD_CHARS.sub("_", row[cols["product"]].strip())
        delivery = row.get("Delivery", "").strip()
        profile = settings.users.get(user)
        user_dir = args.root / ((profile.folder if profile else None) or "")
        folder = user_dir / f"{product} {batch} {lot:%d.%m.%Y} {delivery}".strip()
        name, files = _SCENARIOS[made % len(_SCENARIOS)]
        for f in files:
            path = folder / f.format(batch=batch)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_PDF)
        print(f"  [{name:<9}] {folder.relative_to(args.root)}")
        created.append({"created_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "scenario": name, "user": user,
                        "batch": batch, "lot_created": f"{lot:%d.%m.%Y}", "folder": str(folder)})
        made += 1

    manifest = _append_manifest(args.root, created)
    print(f"Created {made} batch folders under {args.root}")
    print(f"Every created folder is listed in {manifest}")


# Log each generated folder, so test data is easy to find and remove later
def _append_manifest(root: Path, rows: list[dict[str, str]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["created_at", "scenario", "user", "batch", "lot_created", "folder"])
        if new:
            writer.writeheader()
        writer.writerows(rows)
    return path


if __name__ == "__main__":
    main()
