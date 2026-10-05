"""Desktop UI for the Batch Release Autofiler (double-click start.bat)."""
import os
import queue
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from batch_release.config import PROJECT_ROOT, Settings, UserProfile, load_settings
from batch_release.credentials import delete_login, get_login, save_login
from batch_release.planner import NEEDS_ATTENTION, READY, SKIPPED, Request, build_plan
from batch_release.report import load_ledger, summarize, write_report

CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# Design tokens
BG = "#f4f6fb"
CARD = "#ffffff"
BORDER = "#e6e8ef"
TEXT = "#0f172a"
MUTED = "#64748b"
HEADER = "#111827"
ACCENT = "#4f46e5"
ACCENT_HOVER = "#4338ca"
GHOST = "#eef0f6"
GHOST_HOVER = "#e2e5ee"
FONT = "Segoe UI"

MODES = {"Dry run": "dry-run", "Ask before each send": "confirm", "Send all": "auto"}
MODE_HINTS = {
    "dry-run": "Fills every form in Chrome but never presses Send.",
    "confirm": "Fills each form, then waits for you to click Send it / Don't send.",
    "auto": "Sends every ready request without asking. You confirm once before it starts.",
}

# status -> (label, text colour, row tint)
STATUS_STYLE = {
    READY: ("Ready", "#15803d", "#f0fdf4"),
    NEEDS_ATTENTION: ("Needs attention", "#b45309", "#fffbeb"),
    SKIPPED: ("Skipped", "#64748b", "#f8fafc"),
    "FILLED (not sent)": ("Filled (dry run)", "#1d4ed8", "#eff6ff"),
    "FILED": ("Sent", "#047857", "#d1fae5"),
    "FAILED": ("Failed", "#b91c1c", "#fef2f2"),
    "NOT SENT (you declined)": ("Not sent", "#64748b", "#f8fafc"),
}
STAT_TILES = [READY, NEEDS_ATTENTION, SKIPPED, "FILED", "FAILED"]

COLUMNS = [("row", "ROW", 56), ("status", "STATUS", 140), ("batch", "BATCH", 120), ("product", "PRODUCT", 240),
           ("license", "LICENSE", 130), ("lot", "LOT CREATED", 100), ("files", "FILES TO UPLOAD", 300),
           ("message", "NOTES", 420)]


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

    def _ask(self, title: str, text: str, buttons: list[tuple[str, str]]) -> str:
        self._event.clear()
        self.app.call(lambda: self.app.show_banner(title, text, buttons))
        while not self._event.wait(0.2):
            if self.app.stop_requested.is_set():
                self.app.call(self.app.hide_banner)
                raise StopRequested()
        self.app.call(self.app.hide_banner)
        return self._answer or ""

    def wait_for_login(self, retry: bool) -> None:
        if retry:
            self._ask("Not logged in yet",
                      "Finish logging in and wait for the submitted-batches list, then click again.",
                      [("I'm logged in", "ok")])
        elif self.app.login_saved:
            self._ask("Enter the code from your phone",
                      "Your username and password were filled in Chrome. Type the 2FA code there, wait for the "
                      "submitted-batches list, then click:", [("I'm logged in", "ok")])
        else:
            self._ask("Log in to the MOH portal",
                      "Log in in the Chrome window (password + 2FA). When you see the submitted-batches list, "
                      "click:", [("I'm logged in", "ok")])

    def confirm_send(self, req: Request) -> bool:
        return self._ask(f"Send batch {req.batch}?",
                         f"{req.product} is filled in Chrome with {len(req.files)} attachment(s). Check it, then:",
                         [("Send it", "send"), ("Don't send", "skip")]) == "send"


class LoginDialog(ctk.CTkToplevel):
    """Enter / replace / remove the saved portal login of one person."""

    def __init__(self, master: "App", profile: UserProfile) -> None:
        super().__init__(master, fg_color=CARD)
        self.profile = profile
        self.saved = False
        self.title("Portal login")
        self.geometry("460x430")
        self.resizable(False, False)
        self.transient(master)

        ctk.CTkLabel(self, text=f"Portal login — {profile.display_name or profile.sap_user}",
                     font=(FONT, 18, "bold"), text_color=TEXT).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(self, text="Saved encrypted in Windows Credential Manager, for your Windows account only. "
                                "You still type the code from your phone.",
                     font=(FONT, 12), text_color=MUTED, justify="left", wraplength=400).pack(anchor="w", padx=24)
        existing = get_login(profile.sap_user)
        _field_label(self, "ID / username").pack(anchor="w", padx=26, pady=(18, 4))
        self.user = _entry(self, "ת.ז / שם משתמש")
        self.user.pack(fill="x", padx=24)
        if existing:
            self.user.insert(0, existing.username)
        _field_label(self, "Password").pack(anchor="w", padx=26, pady=(12, 4))
        self.pwd = _entry(self, "Type to replace the saved password" if existing else "Password", show="•")
        self.pwd.pack(fill="x", padx=24)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=22)
        _button(row, "Save", self._save, primary=True, width=110).pack(side="right")
        _button(row, "Cancel", self.destroy, width=90).pack(side="right", padx=8)
        if existing:
            _button(row, "Remove saved login", self._remove, width=150, danger=True).pack(side="left")
        self.after(150, lambda: (self.grab_set(), self.pwd.focus() if existing else self.user.focus()))

    def _save(self) -> None:
        if not self.user.get().strip() or not self.pwd.get():
            messagebox.showwarning("Missing", "Enter both username and password.", parent=self)
            return
        save_login(self.profile.sap_user, self.user.get(), self.pwd.get())
        self.saved = True
        self.destroy()

    def _remove(self) -> None:
        delete_login(self.profile.sap_user)
        self.saved = True
        self.destroy()


class App(ctk.CTk):
    """Main window: choose who files, pick the SAP export, check it, then fill/send the requests."""

    def __init__(self) -> None:
        ctk.set_appearance_mode("light")
        super().__init__(fg_color=BG)
        self.title("Batch Release — MOH filing")
        self.geometry("1440x900")
        self.minsize(1150, 700)
        self.after(0, lambda: self.state("zoomed"))  # open maximized, whatever the screen size/scaling

        self.settings: Settings | None = None
        self.profile: UserProfile | None = None
        self.login_saved = False
        self.excel_path: Path | None = None
        self.plan: list[Request] = []
        self.busy = False
        self.stop_requested = threading.Event()
        self.prompts = GuiPrompts(self)
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self._style_table()
        self._build()
        self._load_config()
        self.after(100, self._drain_queue)

    # ---------- layout ----------

    def _build(self) -> None:
        # Header bar
        header = ctk.CTkFrame(self, fg_color=HEADER, corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left", padx=28)
        ctk.CTkLabel(brand, text="Batch Release", font=(FONT, 20, "bold"), text_color="white").pack(anchor="w",
                                                                                                   pady=(9, 0))
        ctk.CTkLabel(brand, text="MOH batch-release filing from the SAP export", font=(FONT, 12),
                     text_color="#9ca3af").pack(anchor="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=24)
        _button(right, "⚙  Settings", lambda: _open(CONFIG_PATH), width=110, dark=True).pack(side="right")
        _button(right, "Login details", self._edit_login, width=120, dark=True).pack(side="right", padx=8)
        self.login_badge = ctk.CTkLabel(right, text="", font=(FONT, 12), corner_radius=12, height=26, padx=10)
        self.login_badge.pack(side="right", padx=8)
        self.profile_menu = ctk.CTkOptionMenu(right, values=["—"], command=self._on_profile, width=200, height=36,
                                              font=(FONT, 13, "bold"), fg_color="#1f2937", button_color="#374151",
                                              button_hover_color="#4b5563", dropdown_font=(FONT, 13),
                                              corner_radius=10)
        self.profile_menu.pack(side="right", padx=8)
        ctk.CTkLabel(right, text="Filing as", font=(FONT, 12), text_color="#9ca3af").pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=14)

        # Step 1 — the SAP export
        file_card = _card(body)
        file_card.pack(fill="x")
        row = ctk.CTkFrame(file_card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 0))
        _step(row, "1", "SAP export").pack(side="left", padx=(0, 16))
        self.excel_entry = _entry(row, "Choose the month's .XLSX export…")
        self.excel_entry.pack(side="left", fill="x", expand=True)
        _button(row, "Browse…", self._browse, width=110).pack(side="left", padx=10)
        self.check_btn = _button(row, "Check file", self._start_plan, primary=True, width=130)
        self.check_btn.pack(side="left")
        self.info_label = ctk.CTkLabel(file_card, text="", font=(FONT, 12), text_color=MUTED, anchor="w")
        self.info_label.pack(fill="x", padx=22, pady=(6, 10))

        # Stat tiles + filter
        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 0))
        self.stat_values: dict[str, ctk.CTkLabel] = {}
        for status in STAT_TILES:
            label, colour, _ = STATUS_STYLE[status]
            tile = _card(stats)
            tile.pack(side="left", padx=(0, 12))
            value = ctk.CTkLabel(tile, text="0", font=(FONT, 22, "bold"), text_color=colour)
            value.pack(side="left", padx=(16, 8), pady=8)
            ctk.CTkLabel(tile, text=label, font=(FONT, 12), text_color=MUTED).pack(side="left", padx=(0, 18))
            self.stat_values[status] = value
        self.filter = ctk.CTkSegmentedButton(stats, values=["To do", "Everything"], font=(FONT, 12),
                                             selected_color=CARD, selected_hover_color=CARD,
                                             unselected_color=GHOST, unselected_hover_color=GHOST_HOVER,
                                             text_color=TEXT, fg_color=GHOST, corner_radius=10, height=34,
                                             command=lambda _: self._refresh_table())
        self.filter.set("To do")
        self.filter.pack(side="right")

        # Table
        table_card = _card(body)  # packed last (see end of _build) so it takes only the space left over
        inner = ctk.CTkFrame(table_card, fg_color=CARD)
        inner.pack(fill="both", expand=True, padx=2, pady=(8, 2))
        self.table = ttk.Treeview(inner, columns=[c[0] for c in COLUMNS], show="headings",
                                  selectmode="extended", style="Modern.Treeview")
        for key, title, width in COLUMNS:
            self.table.heading(key, text=title, anchor="w")
            self.table.column(key, width=width, anchor="w", stretch=key in ("message", "files", "product"))
        for status, (_, colour, tint) in STATUS_STYLE.items():
            self.table.tag_configure(status, background=tint)
        ys = ctk.CTkScrollbar(inner, command=self.table.yview)
        xs = ctk.CTkScrollbar(inner, command=self.table.xview, orientation="horizontal")
        self.table.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(12, 0))
        ys.grid(row=0, column=1, sticky="ns", padx=4)
        xs.grid(row=1, column=0, sticky="ew", padx=12)
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)
        self.table.bind("<Double-1>", self._open_row_folder)
        self.table.bind("<<TreeviewSelect>>", lambda _: self._update_start_label())
        self.empty_label = ctk.CTkLabel(inner, text="Choose who is filing and the SAP export to get started.",
                                        font=(FONT, 14), text_color=MUTED, fg_color=CARD)
        self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        ctk.CTkLabel(table_card, text="Double-click a row to open its attachment folder  ·  Ctrl/Shift-click to "
                                      "file only selected rows", font=(FONT, 11), text_color=MUTED
                     ).pack(anchor="w", padx=20, pady=(0, 10))

        # Banner slot (worker asks you something)
        self.banner_slot = ctk.CTkFrame(body, fg_color="transparent", height=1)
        self.banner = ctk.CTkFrame(self.banner_slot, fg_color="#fffbeb", border_color="#f59e0b", border_width=2,
                                   corner_radius=14)
        texts = ctk.CTkFrame(self.banner, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True, padx=20, pady=8)
        self.banner_title = ctk.CTkLabel(texts, text="", font=(FONT, 15, "bold"), text_color="#92400e")
        self.banner_title.pack(anchor="w")
        self.banner_text = ctk.CTkLabel(texts, text="", font=(FONT, 13), text_color="#78350f", wraplength=950,
                                        justify="left")
        self.banner_text.pack(anchor="w")
        self.banner_buttons = ctk.CTkFrame(self.banner, fg_color="transparent")
        self.banner_buttons.pack(side="right", padx=14)

        # Step 2 — run
        run_card = _card(body)
        controls = ctk.CTkFrame(run_card, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(14, 0))
        _step(controls, "2", "File the requests").pack(side="left", padx=(0, 16))
        _button(controls, "Open results folder", lambda: self.settings and _open(self.settings.results_dir),
                width=160).pack(side="right")
        self.log_btn = _button(controls, "Activity log", self._toggle_log, width=120)
        self.log_btn.pack(side="right", padx=8)
        self.mode = ctk.CTkSegmentedButton(controls, values=list(MODES), font=(FONT, 13), height=40,
                                           selected_color=CARD, selected_hover_color=CARD,
                                           unselected_color=GHOST, unselected_hover_color=GHOST_HOVER,
                                           text_color=TEXT, fg_color=GHOST, corner_radius=10,
                                           command=lambda _: self._update_mode_hint())
        self.mode.set("Dry run")
        self.mode.pack(side="left")
        self.start_btn = _button(controls, "Start", self._start_run, primary=True, width=190, height=42)
        self.start_btn.pack(side="left", padx=14)
        self.stop_btn = _button(controls, "Stop", self._stop, width=100, height=42, danger=True)
        self.stop_btn.pack(side="left")
        line = ctk.CTkFrame(run_card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(8, 0))
        self.mode_hint = ctk.CTkLabel(line, text="", font=(FONT, 12), text_color=MUTED, anchor="w")
        self.mode_hint.pack(side="left")
        self.status_label = ctk.CTkLabel(line, text="", font=(FONT, 12, "bold"), text_color=TEXT, anchor="e")
        self.status_label.pack(side="right")
        self.progress = ctk.CTkProgressBar(run_card, height=6, progress_color=ACCENT, fg_color=GHOST,
                                           corner_radius=3)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(8, 14))
        self.log = ctk.CTkTextbox(run_card, height=110, font=("Cascadia Mono", 11), fg_color=BG, text_color=MUTED,
                                  corner_radius=10, border_width=0)  # shown with the 'Activity log' button
        self._update_mode_hint()
        self._set_busy(False, "")

        # Bottom-up so the run controls always stay on screen and the table fills what remains
        run_card.pack(side="bottom", fill="x")
        self.banner_slot.pack(side="bottom", fill="x")
        table_card.pack(fill="both", expand=True, pady=12)

    def _style_table(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Modern.Treeview", [("Modern.Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Modern.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=36,
                        borderwidth=0, font=(FONT, 10))
        style.map("Modern.Treeview", background=[("selected", "#e0e7ff")], foreground=[("selected", TEXT)])
        style.configure("Modern.Treeview.Heading", background=CARD, foreground=MUTED, font=(FONT, 9, "bold"),
                        relief="flat", borderwidth=0, padding=(6, 10))
        style.map("Modern.Treeview.Heading", background=[("active", BG)])

    # ---------- config / person / file ----------

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
        labels = [p.label for p in self.settings.users.values()]
        self.profile_menu.configure(values=labels or ["(no users in config)"])
        if self.profile is None or self.profile.sap_user not in self.settings.users:
            self.profile = next(iter(self.settings.users.values()), None)
        else:
            self.profile = self.settings.users[self.profile.sap_user]
        if self.profile:
            self.profile_menu.set(self.profile.label)
        self._refresh_profile_info()

    def _on_profile(self, label: str) -> None:
        if self.busy or not self.settings:
            return
        self.profile = next((p for p in self.settings.users.values() if p.label == label), None)
        self.plan = []
        self._refresh_table()
        self._refresh_profile_info()
        if self.excel_entry.get().strip():
            self._start_plan()

    def _refresh_profile_info(self) -> None:
        if not self.settings or not self.profile:
            return
        self.login_saved = get_login(self.profile.sap_user) is not None
        if self.login_saved:
            self.login_badge.configure(text="●  Login saved", fg_color="#064e3b", text_color="#a7f3d0")
        else:
            self.login_badge.configure(text="●  No login saved", fg_color="#78350f", text_color="#fde68a")
        folder = self.settings.attachments_root / (self.profile.folder or "")
        self.info_label.configure(text=f"Only {self.profile.display_name or self.profile.sap_user}'s rows "
                                       f"(Usage dec. made by = {self.profile.sap_user})   ·   Attachments: {folder}")

    def _edit_login(self) -> None:
        if not self.profile or self.busy:
            return
        dialog = LoginDialog(self, self.profile)
        self.wait_window(dialog)
        self._refresh_profile_info()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(title="Choose the SAP export", initialdir=str(Path.home() / "Downloads"),
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
        if not self.settings or not self.profile:
            messagebox.showwarning("No person", "Add the people who file to config.yaml (users:).")
            return
        if not path.is_file():
            messagebox.showwarning("No file", "Choose the SAP export (.xlsx) first.")
            return
        self.excel_path = path
        self._set_busy(True, f"Checking {path.name} and searching {self.profile.display_name}'s folders…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        settings, user = self.settings, self.profile.sap_user

        def work() -> None:
            try:
                plan = build_plan(path, settings, load_ledger(settings.results_dir), only_user=user)
                report = write_report(settings.results_dir, plan, f"plan_{user}")
                self.call(lambda: self._plan_done(plan, report))
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                self.call(lambda: self._failed("Check failed", err))

        threading.Thread(target=work, daemon=True).start()

    def _plan_done(self, plan: list[Request], report: Path) -> None:
        self.plan = plan
        self._stop_progress()
        self._refresh_table()
        counts = summarize(plan)
        who = self.profile.display_name if self.profile else ""
        self._set_busy(False, f"{len(plan)} rows for {who} — {counts.get(READY, 0)} ready to file.")
        self._log(f"Checked {self.excel_path.name if self.excel_path else ''} for {who}: {counts}  "
                  f"(plan saved: {report.name})")

    # ---------- run ----------

    def _selected_ready(self) -> list[Request]:
        by_row = {str(r.excel_row): r for r in self.plan}
        chosen = [by_row[i] for i in self.table.selection() if i in by_row and by_row[i].status == READY]
        return chosen or [r for r in self.plan if r.status == READY]

    def _update_start_label(self) -> None:
        if self.busy:
            return
        n = len(self._selected_ready()) if self.plan else 0
        self.start_btn.configure(text=f"Start  ·  {n} request{'s' if n != 1 else ''}" if n else "Start",
                                 state="normal" if n else "disabled")

    def _update_mode_hint(self) -> None:
        self.mode_hint.configure(text=MODE_HINTS[MODES[self.mode.get()]])

    def _start_run(self) -> None:
        todo = self._selected_ready()
        profile = self.profile
        if not todo or not self.settings or not self.excel_path or not profile:
            return
        mode = MODES[self.mode.get()]
        if mode == "auto" and not messagebox.askyesno(
                "Send without asking?", f"Send {len(todo)} request(s) to the Ministry of Health as "
                                        f"{profile.display_name}, without asking before each one?\n\n"
                                        "They are filed officially under your account."):
            return
        from batch_release.portal import IdentityMismatch  # loads Playwright only when needed
        from batch_release.runner import file_requests

        self.stop_requested.clear()
        self._set_busy(True, "Opening Chrome…", running=True)
        self.progress.set(0)
        settings, excel_name, total = self.settings, self.excel_path.name, len(todo)
        login = get_login(profile.sap_user)

        def on_start(n: int, r: Request) -> None:
            self.call(lambda: self._status(f"[{n}/{total}]  Filling batch {r.batch} — {r.product}"))

        def on_done(n: int, r: Request) -> None:
            def ui() -> None:
                self.progress.set(n / total)
                self._update_row(r)
                self._log(f"[{n}/{total}] {r.batch}: {STATUS_STYLE.get(r.status, (r.status,))[0]} — {r.message}")
            self.call(ui)

        def work() -> None:
            try:
                file_requests(settings, todo, mode, excel_name, self.prompts, on_start=on_start, on_done=on_done,
                              should_stop=self.stop_requested.is_set, profile=profile, login=login)
                msg = "Stopped." if self.stop_requested.is_set() else "Done."
            except StopRequested:
                msg = "Stopped."
            except IdentityMismatch as exc:
                msg = f"Blocked: {exc}"
                self.call(lambda: messagebox.showerror("Wrong account", str(exc)))
            except Exception as exc:
                msg = f"Run aborted: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                self.call(lambda: self._log(traceback.format_exc()))
            report = write_report(settings.results_dir, self.plan, f"results_{mode}_{profile.sap_user}")
            self.call(lambda: self._run_done(msg, report))

        threading.Thread(target=work, daemon=True).start()

    def _run_done(self, msg: str, report: Path) -> None:
        self.hide_banner()
        self._refresh_table()
        self._set_busy(False, msg)
        self._log(f"{msg} Results saved: {report.name}")

    def _toggle_log(self) -> None:
        if self.log.winfo_ismapped():
            self.log.pack_forget()
        else:
            self.log.pack(fill="x", padx=20, pady=(0, 14))

    def _stop(self) -> None:
        self.stop_requested.set()
        self._status("Stopping after the current request…")

    # ---------- banner ----------

    def show_banner(self, title: str, text: str, buttons: list[tuple[str, str]]) -> None:
        self.banner_title.configure(text=title)
        self.banner_text.configure(text=text)
        for child in self.banner_buttons.winfo_children():
            child.destroy()
        for label, value in buttons:
            primary = value in ("ok", "send")
            _button(self.banner_buttons, label, lambda v=value: self.prompts.answer(v), primary=primary,
                    width=140, height=40).pack(side="left", padx=5, pady=12)
        self.banner.pack(fill="x", pady=(0, 12))
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
        if self.plan:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        self._refresh_stats()
        self._update_start_label()

    def _update_row(self, r: Request) -> None:
        iid = str(r.excel_row)
        if self.table.exists(iid):
            self.table.item(iid, values=_row_values(r), tags=(r.status,))
            self.table.see(iid)
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        counts = summarize(self.plan)
        for status, lbl in self.stat_values.items():
            lbl.configure(text=str(counts.get(status, 0)))

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
        self.profile_menu.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if busy:
            self.start_btn.configure(state="disabled")
        else:
            self._update_start_label()
        self._status(text)

    def _stop_progress(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)

    def _status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _log(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")

    def _failed(self, title: str, err: str) -> None:
        self._stop_progress()
        self._set_busy(False, err)
        self._log(err)
        messagebox.showerror(title, err)


# ---------- widget factories (one look across the app) ----------

def _card(master: ctk.CTkBaseClass) -> ctk.CTkFrame:
    return ctk.CTkFrame(master, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)


def _step(master: ctk.CTkBaseClass, number: str, title: str) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(frame, text=number, width=26, height=26, corner_radius=13, fg_color=ACCENT, text_color="white",
                 font=(FONT, 12, "bold")).pack(side="left")
    ctk.CTkLabel(frame, text=title, font=(FONT, 16, "bold"), text_color=TEXT).pack(side="left", padx=10)
    return frame


def _field_label(master: ctk.CTkBaseClass, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=(FONT, 12, "bold"), text_color=TEXT)


def _entry(master: ctk.CTkBaseClass, placeholder: str, show: str = "") -> ctk.CTkEntry:
    return ctk.CTkEntry(master, placeholder_text=placeholder, height=40, corner_radius=10, border_width=1,
                        border_color=BORDER, fg_color="#fbfcfe", text_color=TEXT, font=(FONT, 13), show=show)


def _button(master: ctk.CTkBaseClass, text: str, command: Callable[[], object], primary: bool = False,
            danger: bool = False, dark: bool = False, width: int = 120, height: int = 40) -> ctk.CTkButton:
    if primary:
        colours = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white")
    elif danger:
        colours = dict(fg_color="#fef2f2", hover_color="#fee2e2", text_color="#b91c1c", border_width=1,
                       border_color="#fecaca")
    elif dark:
        colours = dict(fg_color="#1f2937", hover_color="#374151", text_color="#e5e7eb")
    else:
        colours = dict(fg_color=GHOST, hover_color=GHOST_HOVER, text_color=TEXT)
    return ctk.CTkButton(master, text=text, command=command, width=width, height=height, corner_radius=10,
                         font=(FONT, 13, "bold" if primary else "normal"), **colours)


def _row_values(r: Request) -> tuple[str, ...]:
    label = STATUS_STYLE.get(r.status, (r.status,))[0]
    lot = r.lot_created.strftime("%d/%m/%Y") if r.lot_created else ""
    files = ", ".join(p.name for p in r.files)
    return (str(r.excel_row), f"●  {label}", r.batch, r.product, r.license or r.license_raw, lot, files, r.message)


# Open a file or folder with its Windows default program
def _open(path: Path) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        messagebox.showerror("Cannot open", str(exc))


if __name__ == "__main__":
    App().mainloop()
