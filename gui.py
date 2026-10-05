"""Desktop UI for the Batch Release Autofiler (double-click start.bat)."""
import os
import queue
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from batch_release.config import PROJECT_ROOT, Settings, load_settings
from batch_release.planner import NEEDS_ATTENTION, READY, SKIPPED, Request, build_plan
from batch_release.report import load_ledger, summarize, write_report

CONFIG_PATH = PROJECT_ROOT / "config.yaml"

MODES = {
    "Dry run (fill only, never send)": "dry-run",
    "Ask me before each send": "confirm",
    "Send all automatically": "auto",
}

# Row colours in the table, per status
STATUS_STYLE = {
    READY: ("#e8f5e9", "Ready"),
    NEEDS_ATTENTION: ("#fff3e0", "Needs attention"),
    SKIPPED: ("#f2f2f2", "Skipped"),
    "FILLED (not sent)": ("#e3f2fd", "Filled (dry run)"),
    "FILED": ("#c8e6c9", "Sent ✓"),
    "FAILED": ("#ffebee", "Failed"),
    "NOT SENT (you declined)": ("#f2f2f2", "Not sent"),
}

COLUMNS = [("row", "Row", 50), ("status", "Status", 120), ("batch", "Batch", 110), ("product", "Product", 230),
           ("license", "License", 120), ("user", "User", 90), ("lot", "Lot created", 90),
           ("files", "Files to upload", 260), ("message", "Notes", 420)]


class StopRequested(Exception):
    """Raised inside the worker when you press Stop while it waits for you."""


class GuiPrompts:
    """Bridges the browser worker thread and the UI: shows a banner and waits for your click."""

    def __init__(self, app: "App") -> None:
        self.app = app
        self._answer: str | None = None
        self._event = threading.Event()

    # Called from the UI when you click one of the banner buttons
    def answer(self, value: str) -> None:
        self._answer = value
        self._event.set()

    def _ask(self, text: str, buttons: list[tuple[str, str]]) -> str:
        self._event.clear()
        self.app.call(lambda: self.app.show_banner(text, buttons))
        while not self._event.wait(0.2):
            if self.app.stop_requested.is_set():
                self.app.call(self.app.hide_banner)
                raise StopRequested()
        self.app.call(self.app.hide_banner)
        return self._answer or ""

    def wait_for_login(self, retry: bool) -> None:
        text = ("The form could not be opened yet. Finish logging in and wait for the submitted-batches list, "
                "then click again." if retry else
                "Log in in the Chrome window (password + 2FA). When you see the submitted-batches list, click:")
        self._ask(text, [("I'm logged in", "ok")])

    def confirm_send(self, req: Request) -> bool:
        text = f"Batch {req.batch} — {req.product} is filled in Chrome. Check it, then:"
        return self._ask(text, [("Send it", "send"), ("Don't send", "skip")]) == "send"


class App(ctk.CTk):
    """Main window: pick the SAP export, check it, then fill/send the requests."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.title("Batch Release Autofiler — MOH")
        self.geometry("1400x860")
        self.minsize(1100, 650)
        self.after(0, lambda: self.state("zoomed"))  # open maximized, whatever the screen size/scaling

        self.settings: Settings | None = None
        self.excel_path: Path | None = None
        self.plan: list[Request] = []
        self.busy = False
        self.stop_requested = threading.Event()
        self.prompts = GuiPrompts(self)
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self._build()
        self._load_config()
        self.after(100, self._drain_queue)

    # ---------- layout ----------

    def _build(self) -> None:
        bold = ctk.CTkFont(size=14, weight="bold")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(top, text="1. SAP export", font=bold).pack(side="left")
        self.excel_entry = ctk.CTkEntry(top, width=620, placeholder_text="Choose the monthly .XLSX export…")
        self.excel_entry.pack(side="left", padx=10)
        ctk.CTkButton(top, text="Browse…", width=90, command=self._browse).pack(side="left")
        self.check_btn = ctk.CTkButton(top, text="Check file", width=120, command=self._start_plan)
        self.check_btn.pack(side="left", padx=10)
        ctk.CTkButton(top, text="Edit settings", width=110, fg_color="gray60", hover_color="gray45",
                      command=lambda: _open(CONFIG_PATH)).pack(side="right")

        self.info_label = ctk.CTkLabel(self, text="", anchor="w", text_color="gray30")
        self.info_label.pack(fill="x", padx=18)

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(fill="x", padx=16, pady=(8, 4))
        self.chip_labels: dict[str, ctk.CTkLabel] = {}
        for status in (READY, NEEDS_ATTENTION, SKIPPED, "FILED", "FAILED"):
            color, title = STATUS_STYLE[status]
            lbl = ctk.CTkLabel(chips, text=f"{title}: 0", fg_color=color, corner_radius=8, padx=12, height=28)
            lbl.pack(side="left", padx=(0, 8))
            self.chip_labels[status] = lbl
        self.filter = ctk.CTkSegmentedButton(chips, values=["To file + attention", "Everything"],
                                             command=lambda _: self._refresh_table())
        self.filter.set("To file + attention")
        self.filter.pack(side="right")

        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=16, pady=6)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.table = ttk.Treeview(table_frame, columns=[c[0] for c in COLUMNS], show="headings",
                                  selectmode="extended")
        for key, title, width in COLUMNS:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="w", stretch=key in ("message", "files", "product"))
        for status, (color, _) in STATUS_STYLE.items():
            self.table.tag_configure(status, background=color)
        ys = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        xs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        self.table.bind("<Double-1>", self._open_row_folder)
        self.table.bind("<<TreeviewSelect>>", lambda _: self._update_start_label())
        ctk.CTkLabel(self, text="Tip: double-click a row to open its attachment folder · select rows to file only "
                                "those (Ctrl/Shift-click)", text_color="gray45", anchor="w").pack(fill="x", padx=18)

        # Banner that asks you to act (log in / send?); lives in a slot that is empty when not needed
        self.banner_slot = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self.banner_slot.pack(fill="x", padx=16)
        self.banner = ctk.CTkFrame(self.banner_slot, fg_color="#fff8e1", border_color="#f9a825", border_width=2)
        self.banner_label = ctk.CTkLabel(self.banner, text="", font=bold, wraplength=900, justify="left")
        self.banner_label.pack(side="left", padx=14, pady=10)
        self.banner_buttons = ctk.CTkFrame(self.banner, fg_color="transparent")
        self.banner_buttons.pack(side="right", padx=10)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(6, 4))
        ctk.CTkLabel(bottom, text="2. Mode", font=bold).pack(side="left")
        self.mode = ctk.CTkSegmentedButton(bottom, values=list(MODES))
        self.mode.set(next(iter(MODES)))
        self.mode.pack(side="left", padx=10)
        self.start_btn = ctk.CTkButton(bottom, text="Start", width=180, height=36, font=bold,
                                       command=self._start_run, state="disabled")
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ctk.CTkButton(bottom, text="Stop", width=90, height=36, fg_color="#c62828",
                                      hover_color="#8e0000", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")
        ctk.CTkButton(bottom, text="Open results folder", width=150, fg_color="gray60", hover_color="gray45",
                      command=lambda: self.settings and _open(self.settings.results_dir)).pack(side="right")

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.pack(fill="x", padx=16, pady=(4, 2))
        self.progress = ctk.CTkProgressBar(status)
        self.progress.set(0)
        self.progress.pack(fill="x")
        self.status_label = ctk.CTkLabel(status, text="Choose the SAP export and click “Check file”.", anchor="w")
        self.status_label.pack(fill="x")

        self.log = ctk.CTkTextbox(self, height=110, font=("Consolas", 11))
        self.log.pack(fill="x", padx=16, pady=(0, 12))

    # ---------- config / file ----------

    def _load_config(self) -> None:
        if not CONFIG_PATH.exists():
            messagebox.showerror("Settings missing", f"{CONFIG_PATH} not found.\nCopy config.example.yaml to "
                                                     "config.yaml and edit it.")
            return
        try:
            self.settings = load_settings(CONFIG_PATH)
        except Exception as exc:
            messagebox.showerror("Settings error", f"config.yaml could not be read:\n{exc}")
            return
        users = ", ".join(f"{folder or '(whole root)'} ({sap})" for sap, folder in self.settings.users.items())
        self.info_label.configure(text=f"Attachments: {self.settings.attachments_root}     ·     Users: {users}")

    def _browse(self) -> None:
        start = Path.home() / "Downloads"
        path = filedialog.askopenfilename(title="Choose the SAP export", initialdir=str(start),
                                          filetypes=[("Excel", "*.xlsx *.XLSX *.xls"), ("All files", "*.*")])
        if path:
            self.excel_entry.delete(0, "end")
            self.excel_entry.insert(0, path)
            self._start_plan()

    # ---------- check (plan) ----------

    def _start_plan(self) -> None:
        if self.busy:
            return
        self._load_config()
        path = Path(self.excel_entry.get().strip().strip('"'))
        if not self.settings or not path.is_file():
            messagebox.showwarning("No file", "Choose the SAP export (.xlsx) first.")
            return
        self.excel_path = path
        self._set_busy(True, f"Checking {path.name} and searching the attachment folders…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        settings = self.settings

        def work() -> None:
            try:
                plan = build_plan(path, settings, load_ledger(settings.results_dir))
                report = write_report(settings.results_dir, plan, "plan")
                self.call(lambda: self._plan_done(plan, report))
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                self.call(lambda: self._failed("Check failed", err))

        threading.Thread(target=work, daemon=True).start()

    def _plan_done(self, plan: list[Request], report: Path) -> None:
        self.plan = plan
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._refresh_table()
        counts = summarize(plan)
        self._set_busy(False, f"{len(plan)} rows for your users — {counts.get(READY, 0)} ready to file. "
                              f"Plan saved: {report.name}")
        self._log(f"Checked {self.excel_path.name if self.excel_path else ''}: {counts}")

    # ---------- run ----------

    def _selected_ready(self) -> list[Request]:
        by_row = {str(r.excel_row): r for r in self.plan}
        chosen = [by_row[i] for i in self.table.selection() if i in by_row and by_row[i].status == READY]
        return chosen or [r for r in self.plan if r.status == READY]

    def _update_start_label(self) -> None:
        if self.busy:
            return
        n = len(self._selected_ready()) if self.plan else 0
        self.start_btn.configure(text=f"Start ({n})" if n else "Start",
                                 state="normal" if n else "disabled")

    def _start_run(self) -> None:
        todo = self._selected_ready()
        if not todo or not self.settings or not self.excel_path:
            return
        mode = MODES[self.mode.get()]
        if mode == "auto" and not messagebox.askyesno(
                "Send without asking?", f"Send {len(todo)} request(s) to the Ministry of Health without asking "
                                        f"before each one?\n\nThis files them officially under your account."):
            return
        from batch_release.runner import file_requests  # loads Playwright only when needed

        self.stop_requested.clear()
        self._set_busy(True, "Opening Chrome…", running=True)
        self.progress.set(0)
        settings, excel_name, total = self.settings, self.excel_path.name, len(todo)

        def on_start(n: int, r: Request) -> None:
            self.call(lambda: self._status(f"[{n}/{total}] Filling batch {r.batch} — {r.product}"))

        def on_done(n: int, r: Request) -> None:
            def ui() -> None:
                self.progress.set(n / total)
                self._update_row(r)
                self._log(f"[{n}/{total}] {r.batch}: {r.status} — {r.message}")
            self.call(ui)

        def work() -> None:
            try:
                file_requests(settings, todo, mode, excel_name, self.prompts, on_start=on_start, on_done=on_done,
                              should_stop=self.stop_requested.is_set)
                msg = "Stopped." if self.stop_requested.is_set() else "Done."
            except StopRequested:
                msg = "Stopped."
            except Exception as exc:
                msg = f"Run aborted: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                self.call(lambda: self._log(traceback.format_exc()))
            report = write_report(settings.results_dir, self.plan, f"results_{mode}")
            self.call(lambda: self._run_done(f"{msg} Results saved: {report.name}"))

        threading.Thread(target=work, daemon=True).start()

    def _run_done(self, msg: str) -> None:
        self.hide_banner()
        self._refresh_table()
        self._set_busy(False, msg)
        self._log(msg)

    def _stop(self) -> None:
        self.stop_requested.set()
        self._status("Stopping after the current request…")

    # ---------- banner (worker asks you something) ----------

    def show_banner(self, text: str, buttons: list[tuple[str, str]]) -> None:
        self.banner_label.configure(text=text)
        for child in self.banner_buttons.winfo_children():
            child.destroy()
        for title, value in buttons:
            color = "#2e7d32" if value in ("ok", "send") else "gray55"
            ctk.CTkButton(self.banner_buttons, text=title, width=130, height=34, fg_color=color,
                          command=lambda v=value: self.prompts.answer(v)).pack(side="left", padx=4, pady=8)
        self.banner.pack(fill="x", pady=6)
        self.bell()
        self.lift()

    def hide_banner(self) -> None:
        self.banner.pack_forget()

    # ---------- table ----------

    def _refresh_table(self) -> None:
        self.table.delete(*self.table.get_children())
        show_all = self.filter.get() == "Everything"
        for r in self.plan:
            if show_all or r.status != SKIPPED:
                self.table.insert("", "end", iid=str(r.excel_row), values=_row_values(r), tags=(r.status,))
        counts = summarize(self.plan)
        for status, lbl in self.chip_labels.items():
            lbl.configure(text=f"{STATUS_STYLE[status][1]}: {counts.get(status, 0)}")
        self._update_start_label()

    def _update_row(self, r: Request) -> None:
        iid = str(r.excel_row)
        if self.table.exists(iid):
            self.table.item(iid, values=_row_values(r), tags=(r.status,))
            self.table.see(iid)
        counts = summarize(self.plan)
        for status, lbl in self.chip_labels.items():
            lbl.configure(text=f"{STATUS_STYLE[status][1]}: {counts.get(status, 0)}")

    def _open_row_folder(self, _event: object) -> None:
        iid = self.table.focus()
        req = next((r for r in self.plan if str(r.excel_row) == iid), None)
        if req and req.folder:
            _open(req.folder)

    # ---------- helpers ----------

    # Run fn on the UI thread (Tk is not thread-safe)
    def call(self, fn: Callable[[], None]) -> None:
        self._ui_queue.put(fn)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _set_busy(self, busy: bool, text: str, running: bool = False) -> None:
        self.busy = busy
        self.check_btn.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.start_btn.configure(state="disabled") if busy else self._update_start_label()
        self._status(text)

    def _status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _log(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")

    def _failed(self, title: str, err: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._set_busy(False, err)
        self._log(err)
        messagebox.showerror(title, err)


def _row_values(r: Request) -> tuple[str, ...]:
    label = STATUS_STYLE.get(r.status, ("", r.status))[1]
    lot = r.lot_created.strftime("%d/%m/%Y") if r.lot_created else ""
    files = ", ".join(p.name for p in r.files)
    return (str(r.excel_row), label, r.batch, r.product, r.license or r.license_raw, r.decided_by, lot, files,
            r.message)


# Open a file or folder with its Windows default program
def _open(path: Path) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        messagebox.showerror("Cannot open", str(exc))


if __name__ == "__main__":
    App().mainloop()
