# Batch Release Autofiler

Files monthly **הודעת שחרור אצווה לתכשיר רשום** requests on the MOH portal
(`qpbatchrelease.health.gov.il/batch-release`) from the SAP inspection-lot export.

You log in yourself (password + 2FA) in a normal browser window. The script then fills one form
per Excel row, attaches that batch's documents and, depending on the mode, sends it.

## What it does per row

| Form field | Source |
|---|---|
| מספר אצווה | `Batch` |
| תאריך ייצור אצווה | `MFG Date` |
| תפוגת אצווה | `Expiry Date` |
| מספר רישום תכשיר | `GI External License number`: `IL-` dropped, `.` → `-` (`IL-146.13.33189.00` → `146-13-33189-00`) |
| צרופות | files from the batch folder, chosen by the rules below |
| הצהרה checkbox | ticked |

Rows are filed only when `Usage dec. made by` is one of the `users` in `config.yaml`.

**Skipped:** blank or totals rows, non-IL licenses, duplicates of an earlier row (same batch + license +
lot date), and anything already filed in a previous run (`results/filed_ledger.csv`).

**Attachment folder:** searched only inside the user's own folder:
`attachments_root\<user folder>\`. The folder is mapped from the `Usage dec. made by` value under
`users` in `config.yaml`, e.g. `DSABAG01: Dudi`. Folders look like
`ABITREN TEVA 75MG_3ML 10 AMP 44216 25.05.2026 1802347455 ...`, and the batch must appear in the name
as a whole word. If several folders match, the one carrying the **Lot created on** date wins. If that
still isn't unique, the row is marked `NEEDS_ATTENTION` and the candidates are listed.

**Which files are uploaded.** The first rule that applies wins:

1. The folder has a `MOH_SUB` sub-folder → every file inside `MOH_SUB`.
2. A file whose name starts with `OK 3rd P replenish` exists → every file containing that phrase, plus
   the file(s) named after the batch.
3. A file containing `report-` exists → every file containing `report-`, plus every file containing `COA`.
4. None of the above → the row is flagged `NEEDS_ATTENTION`.

Rule 2 without a batch-named file, and rule 3 without a COA, are also flagged. Matching ignores case.

## Setup (once)

```bat
cd C:\personal_projects\batch_release_autofiler
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.yaml config.yaml
```

Edit `config.yaml`: map each SAP username to its folder under `users`, and set `attachments_root`. The script uses your
installed Chrome (`browser_channel: chrome`), so `playwright install` isn't needed.

## Monthly use

```bat
:: 1. Check everything without opening the browser — writes results\plan_*.xlsx
.venv\Scripts\python run.py plan "C:\Users\...\Downloads\05.10.26.XLSX"

:: 2. Fill the forms but DON'T send (screenshots in results\screenshots)
.venv\Scripts\python run.py submit "...\05.10.26.XLSX" --mode dry-run

:: 3. Send, asking y/N before each request
.venv\Scripts\python run.py submit "...\05.10.26.XLSX" --mode confirm

:: 4. Send all without asking (you must type SEND)
.venv\Scripts\python run.py submit "...\05.10.26.XLSX" --mode auto
```

Useful flags: `--limit 1` to process one request only, and `--rows 4 7` for specific Excel rows.

## Testing without the real share

```bat
.venv\Scripts\python tools\make_test_tree.py "...\05.10.26.XLSX" --limit 12
```

This creates `test_data\s_drive\<user folder>\<PRODUCT> <batch> <dd.mm.yyyy> <delivery>\` with dummy
PDFs. The rows cycle through the four attachment cases (MOH_SUB / replenish / report- / nothing), so
`plan` shows each rule working.

## Calibrating the form (first run)

The form fields are located by their Hebrew labels, a best guess made from a screenshot. If a field
isn't found:

```bat
.venv\Scripts\python run.py inspect
```

Log in, open the form and press Enter. This writes `results\form_controls.json` and a screenshot. Then put
the right selectors under `selectors:` in `config.yaml` (see the comments there).

## Notes

- The session lives in `browser_profile\` (git-ignored). You're asked to log in again only when the site
  expires it.
- Nothing is sent in `plan` or `dry-run` mode.
- A failed row is screenshotted and the run carries on. Check the `results_*.xlsx` workbook at the end.
