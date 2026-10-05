"""Build a fake attachments folder tree from an SAP export, for testing on a machine without the real share."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from batch_release.config import PROJECT_ROOT, load_settings  # noqa: E402

# Smallest valid PDF, so the portal accepts the upload
_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
_BAD_CHARS = set('\\/:*?"<>|')


# One folder per selected row: <root>/<year>/<batch> <dd.mm.yyyy>/  (+ a deliberately ambiguous pair)
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", type=Path)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "test_data" / "attachments")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--pattern", default="{batch} {lot:%d.%m.%Y}", help="folder-name pattern")
    args = parser.parse_args()

    settings = load_settings(args.config)
    cols = settings.columns
    df = pd.read_excel(args.excel, dtype=str).fillna("")
    df = df[df[cols["batch"]].str.strip() != ""]
    if settings.users:
        df = df[df[cols["decided_by"]].str.strip().str.upper().isin(settings.users)]
    df = df.head(args.limit)

    made = 0
    for _, row in df.iterrows():
        batch = row[cols["batch"]].strip()
        if _BAD_CHARS & set(batch):
            continue
        lot = pd.to_datetime(row[cols["lot_created"]]).date()
        folder = args.root / f"{lot:%Y}" / args.pattern.format(batch=batch, lot=lot)
        folder.mkdir(parents=True, exist_ok=True)
        for doc in ("COA", "Release certificate"):
            (folder / f"{doc} {batch}.pdf").write_bytes(_PDF)
        made += 1

    # An older submission of the first batch, so the date-disambiguation path gets exercised
    if made:
        first = df.iloc[0]
        older = args.root / "2025" / args.pattern.format(batch=first[cols["batch"]].strip(),
                                                         lot=pd.Timestamp("2025-01-01").date())
        older.mkdir(parents=True, exist_ok=True)
        (older / "old COA.pdf").write_bytes(_PDF)

    print(f"Created {made} batch folders (+1 older duplicate) under {args.root}")


if __name__ == "__main__":
    main()
