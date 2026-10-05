"""Browser automation of the MOH batch-release form (Playwright, your own logged-in session)."""
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from .config import Settings
from .planner import Request
from .report import append_ledger

# How each form element is found. Prefix 'label:' / 'text:' (contains) / 'exact:' / 'role:', else CSS/XPath.
# These are first guesses from the form's Hebrew labels; override them under 'selectors' in config.yaml.
DEFAULT_SELECTORS = {
    "registered_product_button": "exact:אצווה לתכשיר רשום",
    "batch_number": "label:מספר אצווה",
    "mfg_date": "label:תאריך ייצור אצווה",
    "expiry_date": "label:תפוגת אצווה",
    "license": "label:מספר רישום תכשיר",
    "show_product_button": "text:הצג תכשיר",
    "add_file_button": "text:הוסף קובץ",
    "file_input": "input[type=file]",
    "declaration_checkbox": "mat-checkbox",
    "submit_button": "text:שלח בקשה",
}

FILLED = "FILLED (not sent)"
FILED = "FILED"
FAILED = "FAILED"
DECLINED = "NOT SENT (you declined)"


class Portal:
    """Owns the browser window; you log in yourself, then it fills one form per request."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.selectors = {**DEFAULT_SELECTORS, **settings.selectors}
        self.shots_dir = settings.results_dir / "screenshots"
        self._pw = None
        self._browser = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "Portal":
        self._pw = sync_playwright().start()
        # A fresh, cookie-less session every run: the MOH login gateway (idpotp) gets stuck on stale
        # session cookies from an earlier run, and 2FA is needed each run anyway
        # Maximized window and no fixed viewport, so the page always fits your screen (and its scaling)
        self._browser = self._pw.chromium.launch(
            channel=self.settings.browser_channel, headless=False, args=["--start-maximized"])
        self.context = self._browser.new_context(locale="he-IL", no_viewport=True)
        self.page = self.context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.context:
            self.context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    # Open the portal and wait until you have logged in and the request form can be opened
    def wait_for_login(self) -> None:
        page = self._page()
        page.goto(self.settings.portal_url)
        print("\n>>> Log in in the browser window (password + 2FA).")
        while True:
            input(">>> Press Enter here once you see the submitted-batches list... ")
            if "qpbatchrelease" in page.url and "/batch-release" not in page.url:
                page.goto(self.settings.portal_url)  # login sometimes lands on the site root
            if self._open_form(timeout_ms=8000):
                print(">>> Form opened, starting.\n")
                return
            print("    Could not open the form yet (still logging in, or selectors need calibrating — see README).")

    # Fill the form for one request; send it only if mode allows
    def file_request(self, req: Request, mode: str, excel_name: str) -> None:
        page = self._page()
        try:
            page.goto(self.settings.portal_url)
            if not self._open_form(timeout_ms=20000):
                raise RuntimeError("form did not load — session may have expired, log in again")

            self._fill(self.selectors["batch_number"], req.batch)
            self._fill_date(self.selectors["mfg_date"], req.mfg_date)
            self._fill_date(self.selectors["expiry_date"], req.expiry_date)
            self._fill(self.selectors["license"], req.license)
            self._locate(self.selectors["show_product_button"]).first.click()
            page.wait_for_load_state("networkidle")

            self._attach_files(req.files)
            self._tick_declaration()
            shot = self._screenshot(req, "filled")

            if mode == "dry-run":
                req.status, req.message = FILLED, f"dry run, screenshot: {shot.name}"
                return
            if mode == "confirm" and not _ask_yes(f"Send request for batch {req.batch} ({req.product})?"):
                req.status, req.message = DECLINED, f"screenshot: {shot.name}"
                return

            self._locate(self.selectors["submit_button"]).first.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            done = self._screenshot(req, "sent")
            reference = _find_reference(page.inner_text("body"))
            req.status, req.message = FILED, f"reference: {reference or '?'}, screenshot: {done.name}"
            append_ledger(self.settings.results_dir, req, excel_name, reference)
        except Exception as exc:  # one bad row must not stop the whole batch
            shot = self._screenshot(req, "error")
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            req.status, req.message = FAILED, f"{type(exc).__name__}: {first_line} (screenshot: {shot.name})"

    # Dump every input/button on the current page, used to calibrate the selectors
    def inspect_form(self, out_path: Path) -> Path:
        controls = self._page().evaluate("""() => [...document.querySelectorAll(
            'input, textarea, select, button, [role=button], [role=checkbox], mat-select, label')].map(e => ({
                tag: e.tagName.toLowerCase(), type: e.getAttribute('type'), id: e.id || null,
                name: e.getAttribute('name'), formcontrolname: e.getAttribute('formcontrolname'),
                placeholder: e.getAttribute('placeholder'), aria: e.getAttribute('aria-label'),
                for: e.getAttribute('for'), text: (e.innerText || '').trim().slice(0, 80),
                visible: !!(e.offsetWidth || e.offsetHeight)
            }))""")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path

    def _page(self) -> Page:
        assert self.page is not None, "use Portal inside a 'with' block"
        return self.page

    # Turn a selector spec from DEFAULT_SELECTORS/config into a Playwright locator
    def _locate(self, spec: str) -> Locator:
        page = self._page()
        kind, _, value = spec.partition(":")
        if kind == "label" and value:
            return page.get_by_label(value, exact=False)
        if kind == "text" and value:
            return page.get_by_text(value, exact=False)
        if kind == "exact" and value:
            return page.get_by_text(value, exact=True)
        if kind == "role" and value:
            return page.get_by_role(value)  # type: ignore[arg-type]
        return page.locator(spec)

    # After login the portal shows the submitted-batches list; the form opens from the
    # 'אצווה לתכשיר רשום' button (not the 'תקנה 29' one next to it)
    def _open_form(self, timeout_ms: int) -> bool:
        if self._form_ready(timeout_ms=2000):
            return True
        button = self._locate(self.selectors["registered_product_button"]).first
        try:
            button.wait_for(state="visible", timeout=timeout_ms)
            button.click()
        except Exception:
            return False
        return self._form_ready(timeout_ms=timeout_ms)

    def _form_ready(self, timeout_ms: int) -> bool:
        try:
            self._locate(self.selectors["batch_number"]).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def _fill(self, spec: str, value: str) -> None:
        field = self._locate(spec).first
        field.fill(value)
        field.press("Tab")

    # Type the date as text in the portal's format, then close any date-picker popup
    def _fill_date(self, spec: str, value: date | None) -> None:
        assert value is not None
        field = self._locate(spec).first
        field.fill(value.strftime(self.settings.portal_date_format))
        field.press("Escape")
        field.press("Tab")

    # The declaration is an Angular Material checkbox: the real <input> is hidden under the styled box,
    # so click the box itself and confirm it really got ticked
    def _tick_declaration(self) -> None:
        box = self._locate(self.selectors["declaration_checkbox"]).last
        box.scroll_into_view_if_needed()
        tick = box.locator("input[type=checkbox]")
        if not tick.is_checked():
            box.locator("label").first.click()
        if not tick.is_checked():
            raise RuntimeError("declaration checkbox did not get ticked")

    # Prefer the hidden <input type=file>; fall back to clicking 'add file' and answering the dialog
    def _attach_files(self, files: list[Path]) -> None:
        page = self._page()
        inputs = self._locate(self.selectors["file_input"])
        for path in files:
            if inputs.count() > 0:
                inputs.last.set_input_files(str(path))
            else:
                with page.expect_file_chooser() as chooser:
                    self._locate(self.selectors["add_file_button"]).first.click()
                chooser.value.set_files(str(path))
            page.wait_for_load_state("networkidle")
            if not page.get_by_text(path.name, exact=False).count():
                time.sleep(1.0)  # give slow uploads a moment before the next file

    def _screenshot(self, req: Request, tag: str) -> Path:
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        safe_batch = re.sub(r"[^A-Za-z0-9._-]", "_", req.batch)
        path = self.shots_dir / f"{datetime.now():%Y%m%d_%H%M%S}_row{req.excel_row}_{safe_batch}_{tag}.png"
        try:
            self._page().screenshot(path=str(path), full_page=True)
        except Exception:
            pass
        return path


# Console yes/no prompt for confirm mode
def _ask_yes(question: str) -> bool:
    return input(f">>> {question} [y/N] ").strip().lower() in ("y", "yes")


# Best-effort grab of the request/reference number shown after sending
def _find_reference(page_text: str) -> str:
    m = re.search(r"(?:מספר\s*(?:בקשה|פניה|פנייה|אסמכתא)|reference)\D{0,10}(\d{4,})", page_text, re.IGNORECASE)
    return m.group(1) if m else ""
