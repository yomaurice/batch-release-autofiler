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

**One person per run (guard).** You choose who is filing ("Filing as"), and only rows whose
`Usage dec. made by` is that person are loaded, so Esther can't file Gil's batches. After login the app
also reads the portal greeting (`שלום, <name>`). If it doesn't match that person's `portal_name` in
`config.yaml`, the run is blocked before anything is filled.

**Skipped:** blank or totals rows, rows with an empty `UD code`, non-IL licenses, duplicates of an earlier row (same batch + license +
lot date), and anything already submitted in a previous run (see *Submitted requests* below).

**Attachment folder:** searched only inside the user's own folder:
`attachments_root\<user folder>\`. The folder is mapped from the `Usage dec. made by` value under
`users` in `config.yaml` (`folder: Dudi`). Folders look like
`ABITREN TEVA 75MG_3ML 10 AMP 44216 25.05.2026 1802347455 ...`, and the batch must appear in the name
as a whole word. If several folders match, they are **combined and treated as one folder**: the rules
below look at the files of all of them together. Folders whose name carries a *different* date than
**Lot created on** belong to another lot and are left out. If every matching folder carries a different
date, the row is marked `NEEDS_ATTENTION` and the candidates are listed.

**Which files are uploaded.** The first rule that applies wins:

1. A `MOH_SUB` sub-folder exists → every file inside `MOH_SUB` (nothing outside it).
2. A file whose name starts with `OK 3rd P replenish` exists → every file containing that phrase (data
   logger), plus the **latest** file named exactly as the batch (e.g. `44216.pdf`), which is the COA here.
3. A file containing `report-` exists → every file containing `report-`, plus the **latest** file containing `COA`.
4. A file containing `data logger` exists → every file containing `data logger`, plus the **latest** file
   containing `COA`.
5. None of the above → the row is flagged `NEEDS_ATTENTION`. A COA alone is never uploaded.

"Latest" means most recently modified. A rule whose COA is missing is also flagged. Matching ignores case.

**After a real send**, the confirmation page (with its request number) is screenshotted into
`results\screenshots\`, and a copy is saved **into the batch's own folder(s)** as
`MOH confirmation <batch> <number>.png`. Dry runs never do this.

**Submitted requests log.** Every request sent is added to `results\Submitted requests.xlsx` (newest first):
time, MOH request number (read from the confirmation page that is screenshotted), batch, product, license,
dates, who filed it, the SAP export, the files uploaded, the screenshot and the batch folder(s). It is one
cumulative file across all runs. It is safe to keep it open in Excel: each submission is first recorded in
`results\submissions.jsonl` (never opened by people, also used to skip already-filed batches), and the Excel
file is rebuilt from it. If it is open at that moment, an up-to-date `Submitted requests - updated copy.xlsx`
is written instead, and the main file catches up as soon as it is closed (or when you click **Submitted
requests** in the app).

## Setup on a PC (once)

Needs **Git**, **Python 3.11+** (with the `py` launcher) and **Google Chrome**.

```bat
cd C:\
git clone https://github.com/yomaurice/batch-release-autofiler
```

Then double-click **`start.bat`** in that folder. The first time it creates the Python environment and
installs the packages (a minute or two), then opens the app. Create a desktop shortcut to `start.bat` if
you like.

In the app, click **⚙ Settings** and fill in:
- **Attachments root**: the S: folder that holds one sub-folder per person
  (`S:\Batch ready for release\AA Pending QP Release\AA RELEASED & BEFORE MOH PORTAL`).
- **People who file**: for each person, their SAP user (as in `Usage dec. made by`), their sub-folder, and
  their **portal name**, exactly as in the portal's `שלום, ...` greeting.
- **Run modes**: which modes the app offers, and which one is selected when it opens.

Settings are saved in `config.yaml` (not in git, so each PC keeps its own). **Open config file** in Settings
shows the file itself. The app uses your installed Chrome, so `playwright install` isn't needed.

**Login details (optional, once per person and PC).** In the app, choose your name and click **Login
details**. Your portal username and password are saved encrypted in **Windows Credential Manager**, for your
Windows account only, never in a file. Each run then fills them on the MOH login page, and you type only the
code from your phone.

### Updating to a new version

```bat
cd C:\batch-release-autofiler
git pull
```

Then start the app with `start.bat` as usual. It installs any new packages by itself. Your `config.yaml`,
saved logins and `results\` are kept.

## Monthly use — the app

Double-click **`start.bat`**. The first time, it sets up Python packages, which takes a minute.

0. Pick your name under **Filing as** (top right).
1. **Browse…** to the month's SAP export. It is checked straight away: every one of your rows is shown,
   colour-coded **Ready** / **Needs attention** / **Skipped**, with the files that will be uploaded and
   the reason for anything not ready. Double-click a row to open its folder.
2. Pick a **mode**: *Dry run* (fill only, never send), *Ask before each send*, or *Send all*. Which modes
   are offered, and which one is selected at start-up, is set under **Run modes** in Settings (e.g. offer
   only *Send all* once things are stable).
3. **Start**. This files every Ready row, or only the rows you selected (Ctrl/Shift-click). Chrome opens.
   Your username and password are filled in if saved. Type the 2FA code, then click **I'm logged in** in
   the yellow banner. In *ask* mode the banner shows
   **Send it / Don't send** for each filled form. **Stop** finishes the current request and stops.

Every check and run is saved as an Excel file in `results\` (**Open results folder**).

### Command line (same engine)

```bat
.venv\Scripts\python run.py plan   "...\05.10.26.XLSX"
.venv\Scripts\python run.py submit "...\05.10.26.XLSX" --user DSABAG01 [--mode dry-run|confirm|auto] [--limit 1] [--rows 4 7]
```

## Testing without the real share

```bat
.venv\Scripts\python tools\make_test_tree.py "...\05.10.26.XLSX" --limit 12
```

This creates `test_data\s_drive\<user folder>\<PRODUCT> <batch> <dd.mm.yyyy> <delivery>\` with dummy
PDFs. The rows cycle through the attachment cases (MOH_SUB / replenish / report- / data logger / nothing), so
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
- **Website popups.** Every popup or message box the portal shows during a run (also ones that close again
  by themselves) is written to `results\website_messages.log`, with the time, row and batch. An error popup
  (`שגיאה`, ...) stops that request: it is marked **Failed** with the website's own message, and the run
  moves on to the next one. Such errors can be the portal's fault, but they are always recorded.
- `results\` is per PC: the *Submitted requests* log and the "already filed" check only know about
  requests sent from that PC.
