"""Filing loop shared by the command line and the desktop UI."""
import time
from collections.abc import Callable

from .config import Settings
from .planner import Request
from .portal import Portal, Prompts


# Open the browser, wait for your login, then fill (and depending on mode, send) each request in turn
def file_requests(settings: Settings, requests: list[Request], mode: str, excel_name: str, prompts: Prompts,
                  on_start: Callable[[int, Request], None] = lambda n, r: None,
                  on_done: Callable[[int, Request], None] = lambda n, r: None,
                  should_stop: Callable[[], bool] = lambda: False,
                  pause_s: float = 2.0) -> None:
    with Portal(settings, prompts) as portal:
        portal.wait_for_login()
        for n, req in enumerate(requests, 1):
            if should_stop():
                return
            on_start(n, req)
            portal.file_request(req, mode, excel_name)
            on_done(n, req)
            if n < len(requests):
                time.sleep(pause_s)
