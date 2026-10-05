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

1. The folder has a `MOH_SUB` sub-folder → every file inside `MOH_SUB` (nothing outside it).
2. A file whose name starts with `OK 3rd P replenish` exists → every file containing that phrase, plus the
   file named **exactly** as the batch (e.g. `44216.pdf`).
3. A file containing `report-` exists → every file containing `report-`, plus every file containing
   `COA`, plus the file named **exactly** as the batch.
4. None of the above → the row is flagged `NEEDS_ATTENTION`. A COA alone is never uploaded.

A rule whose batch file (or, for rule 3, COA) is missing is also flagged. Matching ignores case.

**After a real send**, the confirmation page (with its request number) is screenshotted into
`results\screenshots\`, and a copy is saved **into the batch's own folder** as
`MOH confirmation <batch> <number>.png`. Dry runs never do this.

## Setup (once)

```bat
cd C:\personal_projects\batch_release_autofiler
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.yaml config.yaml
```

Edit `config.yaml`: map each SAP username to its folder under `users`, and set `attachments_root`. The script uses your
installed Chrome (`browser_channel: chrome`), so `playwright install` isn't needed.

## Monthly use — the app

Double-click **`start.bat`**. The first time, it sets up Python packages, which takes a minute.

1. **Browse…** to the month's SAP export. It is checked straight away: every row for your users is shown,
   colour-coded **Ready** / **Needs attention** / **Skipped**, with the files that will be uploaded and
   the reason for anything not ready. Double-click a row to open its folder.
2. Pick a **mode**: *Dry run* (fill only, never send), *Ask me before each send*, or *Send all
   automatically*.
3. **Start**. This files every Ready row, or only the rows you selected (Ctrl/Shift-click). Chrome opens.
   Log in with 2FA, then click **I'm logged in** in the yellow banner. In *ask* mode the banner shows
   **Send it / Don't send** for each filled form. **Stop** finishes the current request and stops.

Every check and run is saved as an Excel file in `results\` (**Open results folder**).

### Command line (same engine)

```bat
.venv\Scripts\python run.py plan   "...\05.10.26.XLSX"
.venv\Scripts\python run.py submit "...\05.10.26.XLSX" --mode dry-run|confirm|auto [--limit 1] [--rows 4 7]
```

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

- Each run opens a fresh browser session, so you log in (with 2FA) at the start of every run. Old
  session cookies made the MOH login gateway hang.
- Nothing is sent in `plan` or `dry-run` mode.
- On the attachments share (S:), the only thing ever written is the confirmation screenshot, saved into a
  batch folder after a real send. Everything else stays in the project folder (`results\`, `test_data\`).
  Each generated test tree logs its folders in `test_data\<root>\_test_tree_manifest.csv`.
- To switch from testing to the real share, change `attachments_root` in `config.yaml`. Nothing else changes.
- A failed row is screenshotted and the run carries on. Check the `results_*.xlsx` workbook at the end.
