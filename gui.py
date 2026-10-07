"""Desktop UI for the Batch Release Autofiler (double-click start.bat)."""
import os
import queue
import shutil
import threading
import traceback
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from batch_release.config import BUNDLE_ROOT, PROJECT_ROOT, Settings, UserProfile, load_settings
from batch_release.config_store import save_changes
from batch_release.credentials import delete_login, get_login, save_login
from batch_release.planner import NEEDS_ATTENTION, READY, SKIPPED, Request, build_plan
from batch_release.report import (load_ledger, refresh_submissions_log, submissions_log_path, summarize,
                                   write_report)

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


class SettingsDialog(ctk.CTkToplevel):
    """Edit config.yaml through a form; saving writes the file (its comments are kept)."""

    COLUMN_LABELS = {"batch": "Batch", "mfg_date": "MFG date", "expiry_date": "Expiry date",
                     "license": "License number", "lot_created": "Lot created on", "decided_by": "Usage decision by",
                     "product": "Product text", "ud_code": "UD code"}

    def __init__(self, master: "App", settings: Settings) -> None:
        super().__init__(master, fg_color=BG)
        self.saved = False
        self.title("Settings")
        height = min(820, self.winfo_screenheight() - 110)  # keep the Save bar on small / scaled screens
        self.geometry(f"900x{height}+{max(0, (self.winfo_screenwidth() - 900) // 2)}+20")
        self.minsize(760, 480)
        self.transient(master)

        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=70)
        bar.pack(fill="x", side="bottom")
        _button(bar, "Save", self._save, primary=True, width=120).pack(side="right", padx=(8, 24), pady=14)
        _button(bar, "Cancel", self.destroy, width=100).pack(side="right", pady=14)
        _button(bar, "Open config file", lambda: _open(CONFIG_PATH), width=150).pack(side="left", padx=(24, 8))
        _button(bar, "Test browser", self._test_browser, width=130).pack(side="left")

        body = ctk.CTkScrollableFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        # Run modes
        card = self._section(body, "Run modes", "Choose which modes appear in the app, and which one is "
                                                "selected when it opens. Once things are stable, offer only "
                                                "'Send all'.")
        self.mode_switches: dict[str, ctk.CTkSwitch] = {}
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(4, 0))
        for label, mode in MODES.items():
            sw = ctk.CTkSwitch(row, text=label, font=(FONT, 13), text_color=TEXT, progress_color=ACCENT,
                               command=self._refresh_default_mode)
            sw.pack(side="left", padx=(0, 28))
            if mode in settings.modes:
                sw.select()
            self.mode_switches[mode] = sw
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=20, pady=(14, 18))
        _field_label(line, "Selected when the app opens").pack(side="left", padx=(0, 14))
        self.default_mode = ctk.CTkSegmentedButton(line, values=list(MODES), font=(FONT, 13), height=36,
                                                   selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                                   unselected_color=GHOST, unselected_hover_color=GHOST_HOVER,
                                                   text_color=TEXT, fg_color=GHOST, corner_radius=10)
        self.default_mode.pack(side="left")
        self.default_mode.set(_mode_label(settings.default_mode))
        self._refresh_default_mode()

        # Folders and browser
        card = self._section(body, "Folders and browser", "")
        self.attachments_root = self._path_row(card, "Attachments root (S: drive)", settings.attachments_root)
        self.results_dir = self._path_row(card, "Results folder", settings.results_dir)
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=20, pady=(10, 18))
        _field_label(line, "Folder levels searched").pack(side="left", padx=(0, 10))
        self.depth = ctk.CTkOptionMenu(line, values=[str(n) for n in range(1, 9)], width=70, height=34,
                                       font=(FONT, 13), fg_color=GHOST, button_color=GHOST_HOVER,
                                       button_hover_color=BORDER, text_color=TEXT, corner_radius=10)
        self.depth.set(str(settings.folder_search_depth))
        self.depth.pack(side="left")
        _field_label(line, "Browser").pack(side="left", padx=(36, 10))
        self.browser = ctk.CTkSegmentedButton(line, values=["Chrome", "Edge"], font=(FONT, 13), height=34,
                                              selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                              unselected_color=GHOST, unselected_hover_color=GHOST_HOVER,
                                              text_color=TEXT, fg_color=GHOST, corner_radius=10)
        self.browser.set("Edge" if settings.browser_channel == "msedge" else "Chrome")
        self.browser.pack(side="left")

        # People
        card = self._section(body, "People who file",
                             "SAP user = 'Usage dec. made by' in the export. Portal name = the name in the "
                             "portal's 'שלום, …' greeting, used to check who is logged in.")
        self.users_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.users_frame.pack(fill="x", padx=20)
        for col, title in enumerate(("SAP user", "Name shown", "Folder under attachments root", "Portal name", "")):
            ctk.CTkLabel(self.users_frame, text=title, font=(FONT, 11, "bold"), text_color=MUTED,
                         anchor="w").grid(row=0, column=col, sticky="w", padx=4)
        for col, weight in enumerate((2, 2, 3, 2, 0)):
            self.users_frame.grid_columnconfigure(col, weight=weight)
        self.user_rows: list[list[ctk.CTkBaseClass]] = []
        self._next_user_row = 1
        for p in settings.users.values():
            self._add_user_row(p.sap_user, p.display_name, p.folder or "", p.portal_name)
        _button(card, "+  Add person", self._add_user_row, width=130, height=34).pack(anchor="w", padx=20,
                                                                                    pady=(8, 18))

        # Advanced
        card = self._section(body, "Advanced", "Column names in the SAP export, and the portal address. Change "
                                               "only if the export or the portal changes.")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(0, 18))
        grid.grid_columnconfigure((1, 3), weight=1)
        self.columns: dict[str, ctk.CTkEntry] = {}
        for i, (key, label) in enumerate(self.COLUMN_LABELS.items()):
            ctk.CTkLabel(grid, text=label, font=(FONT, 12), text_color=TEXT, anchor="w").grid(
                row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0 if i % 2 == 0 else 20, 8), pady=4)
            entry = self._entry(grid, settings.columns.get(key, ""))
            entry.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="ew", pady=4)
            self.columns[key] = entry
        last = (len(self.COLUMN_LABELS) + 1) // 2
        ctk.CTkLabel(grid, text="Portal address", font=(FONT, 12), text_color=TEXT, anchor="w").grid(
            row=last, column=0, sticky="w", pady=(12, 4))
        self.portal_url = self._entry(grid, settings.portal_url)
        self.portal_url.grid(row=last, column=1, columnspan=3, sticky="ew", pady=(12, 4))

        self.after(150, self.grab_set)

    # ---------- building blocks ----------

    def _section(self, master: ctk.CTkBaseClass, title: str, hint: str) -> ctk.CTkFrame:
        card = _card(master)
        card.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(card, text=title, font=(FONT, 16, "bold"), text_color=TEXT).pack(anchor="w", padx=20,
                                                                                       pady=(16, 2))
        if hint:
            ctk.CTkLabel(card, text=hint, font=(FONT, 12), text_color=MUTED, justify="left", wraplength=780,
                         anchor="w").pack(anchor="w", padx=20, pady=(0, 8))
        return card

    def _entry(self, master: ctk.CTkBaseClass, value: str, placeholder: str = "") -> ctk.CTkEntry:
        entry = ctk.CTkEntry(master, placeholder_text=placeholder, height=34, corner_radius=8, border_width=1,
                             border_color=BORDER, fg_color="#fbfcfe", text_color=TEXT, font=(FONT, 13))
        if value:
            entry.insert(0, value)
        return entry

    def _path_row(self, master: ctk.CTkBaseClass, label: str, value: Path) -> ctk.CTkEntry:
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(row, text=label, font=(FONT, 12, "bold"), text_color=TEXT, width=210, anchor="w").pack(
            side="left")
        entry = self._entry(row, _display_path(value))
        entry.pack(side="left", fill="x", expand=True)

        def browse() -> None:
            chosen = filedialog.askdirectory(parent=self, initialdir=str(value if value.exists() else Path.home()))
            if chosen:
                entry.delete(0, "end")
                entry.insert(0, _display_path(Path(chosen)))
        _button(row, "Browse…", browse, width=90, height=34).pack(side="left", padx=(8, 0))
        return entry

    def _add_user_row(self, sap: str = "", name: str = "", folder: str = "", portal: str = "") -> None:
        r = self._next_user_row
        self._next_user_row += 1
        cells: list[ctk.CTkBaseClass] = [self._entry(self.users_frame, v, ph) for v, ph in
                                         ((sap, "e.g. DSABAG01"), (name, "e.g. Dudi"), (folder, "e.g. Dudi"),
                                          (portal, "e.g. דוד"))]
        for c, cell in enumerate(cells):
            cell.grid(row=r, column=c, sticky="ew", padx=4, pady=3)
        cells[3].configure(justify="right")
        widgets = list(cells)
        remove = ctk.CTkButton(self.users_frame, text="✕", width=34, height=34, corner_radius=8, fg_color=GHOST,
                               hover_color="#fee2e2", text_color=MUTED, font=(FONT, 13),
                               command=lambda: self._remove_user_row(widgets))
        remove.grid(row=r, column=4, padx=4, pady=3)
        widgets.append(remove)
        self.user_rows.append(widgets)

    def _remove_user_row(self, widgets: list[ctk.CTkBaseClass]) -> None:
        for w in widgets:
            w.destroy()
        self.user_rows.remove(widgets)

    # Uses the saved settings (browser, portal address); Chrome opens for a few seconds
    def _test_browser(self) -> None:
        from batch_release.portal import browser_selftest  # loads Playwright only when needed
        self.configure(cursor="watch")
        self.update()
        try:
            messagebox.showinfo("Browser test", "Works: " + browser_selftest(load_settings(CONFIG_PATH)),
                                parent=self)
        except Exception as exc:
            messagebox.showerror("Browser test", f"The app could not open the browser:\n{type(exc).__name__}: "
                                                 f"{str(exc).strip().splitlines()[0] if str(exc).strip() else ''}",
                                 parent=self)
        finally:
            self.configure(cursor="")

    def _refresh_default_mode(self) -> None:
        offered = [label for label, mode in MODES.items() if self.mode_switches[mode].get()]
        self.default_mode.configure(values=offered or ["—"])
        if self.default_mode.get() not in offered:
            self.default_mode.set(offered[0] if offered else "—")

    # ---------- save ----------

    def _save(self) -> None:
        modes = [mode for mode, sw in self.mode_switches.items() if sw.get()]
        if not modes:
            messagebox.showwarning("Run modes", "Turn on at least one mode.", parent=self)
            return
        users: dict[str, dict[str, str]] = {}
        for cells in self.user_rows:
            sap, name, folder, portal = (c.get().strip() for c in cells[:4])  # type: ignore[attr-defined]
            if not (sap or name or folder or portal):
                continue
            if not sap:
                messagebox.showwarning("People", f"Fill in the SAP user for '{name or folder}'.", parent=self)
                return
            if sap.upper() in users:
                messagebox.showwarning("People", f"{sap.upper()} is listed twice.", parent=self)
                return
            users[sap.upper()] = {"display_name": name, "folder": folder, "portal_name": portal}
        missing_portal = [s for s, u in users.items() if not u["portal_name"]]
        if missing_portal and not messagebox.askyesno(
                "Portal name missing", f"No portal name for {', '.join(missing_portal)}: their runs will be "
                                       "blocked after login. Save anyway?", parent=self):
            return
        root = self.attachments_root.get().strip().strip('"')
        if not root:
            messagebox.showwarning("Folders", "Set the attachments root.", parent=self)
            return
        changes = {
            "modes": modes,
            "default_mode": MODES[self.default_mode.get()],
            "attachments_root": root,
            "results_dir": self.results_dir.get().strip().strip('"') or "results",
            "folder_search_depth": int(self.depth.get()),
            "browser_channel": "msedge" if self.browser.get() == "Edge" else "chrome",
            "users": users,
            "columns": {k: e.get().strip() for k, e in self.columns.items() if e.get().strip()},
            "portal_url": self.portal_url.get().strip(),
        }
        try:
            save_changes(CONFIG_PATH, changes)
            load_settings(CONFIG_PATH)  # make sure what was written reads back
        except Exception as exc:
            messagebox.showerror("Could not save", f"{type(exc).__name__}: {exc}", parent=self)
            return
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
        self._mode_ready = False

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
        _button(right, "⚙  Settings", self._edit_settings, width=110, dark=True).pack(side="right")
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
        _button(controls, "Submitted requests", self._open_submissions_log, width=160).pack(side="right", padx=(8, 0))
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

    def _load_config(self, reset_mode: bool = False) -> None:
        example = BUNDLE_ROOT / "config.example.yaml"
        if not CONFIG_PATH.exists() and example.exists():  # first start: begin from the example settings
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(example, CONFIG_PATH)
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
        # Offer only the modes enabled in Settings; keep your current choice unless it is no longer offered
        offered = [label for label, mode in MODES.items() if mode in self.settings.modes]
        self.mode.configure(values=offered)
        if reset_mode or not self._mode_ready or self.mode.get() not in offered:
            self.mode.set(_mode_label(self.settings.default_mode))
            self._mode_ready = True
        self._update_mode_hint()
        threading.Thread(target=refresh_submissions_log, args=(self.settings.results_dir,), daemon=True).start()

    # Bring the log up to date (it may have missed updates while it was open in Excel), then open it
    def _open_submissions_log(self) -> None:
        if not self.settings:
            return
        note = refresh_submissions_log(self.settings.results_dir, retries=1)
        path = submissions_log_path(self.settings.results_dir)
        if not path.exists():
            messagebox.showinfo("Submitted requests", "Nothing has been submitted yet.")
        elif note:
            messagebox.showinfo("Submitted requests", note[0].upper() + note[1:] + ".")
        else:
            _open(path)

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

    def _edit_settings(self) -> None:
        if self.busy:
            messagebox.showinfo("Settings", "Settings can be changed when no check or run is in progress.")
            return
        if not self.settings:
            _open(CONFIG_PATH)  # config.yaml has an error the form can't show, so fix it in the file
            return
        dialog = SettingsDialog(self, self.settings)
        self.wait_window(dialog)
        if dialog.saved:
            self._load_config(reset_mode=True)
            self.plan = []
            self._refresh_table()

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
                msg, reason = f"Blocked: {exc}", str(exc)
                self.call(lambda: messagebox.showerror("Wrong account", reason))
            except Exception as exc:
                msg = f"Run aborted: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                details = traceback.format_exc()
                self.call(lambda: self._log(details))
            report = write_report(settings.results_dir, self.plan, f"results_{mode}_{profile.sap_user}")
            log_note = refresh_submissions_log(settings.results_dir)
            if log_note:
                self.call(lambda: self._log(log_note))
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
        for folder in req.folders if req else []:
            _open(folder)

    # ---------- helpers ----------

    # Run fn on the UI thread (Tk is not thread-safe)
    def call(self, fn: Callable[[], None]) -> None:
        self._ui_queue.put(fn)

    # One failing UI update must not stop the rest (the window would freeze with buttons disabled)
    def _drain_queue(self) -> None:
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    self._ui_error(traceback.format_exc())
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_queue)

    # The app runs without a console, so UI errors go to the activity log and results/app_errors.log
    def _ui_error(self, details: str) -> None:
        try:
            self._log(details)
            if self.settings:
                self.settings.results_dir.mkdir(parents=True, exist_ok=True)
                with (self.settings.results_dir / "app_errors.log").open("a", encoding="utf-8") as f:
                    f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{details}\n")
        except Exception:
            pass

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
def _mode_label(mode: str) -> str:
    return next(label for label, m in MODES.items() if m == mode)


# Paths inside the project are shown relative (test_data\s_drive), others in full (S:\...)
def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _open(path: Path) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        messagebox.showerror("Cannot open", str(exc))


# BatchRelease.exe --selftest: check the browser can be driven, write the outcome next to config.yaml
def _selftest() -> None:
    from batch_release.portal import browser_selftest
    try:
        result = "OK: " + browser_selftest(load_settings(CONFIG_PATH))
    except Exception:
        result = "FAILED:\n" + traceback.format_exc()
    (PROJECT_ROOT / "selftest.txt").write_text(result, encoding="utf-8")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        App().mainloop()
