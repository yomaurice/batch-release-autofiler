"""Command-line entry point: plan / submit / inspect."""
import argparse
import sys
from pathlib import Path

from batch_release.config import MODES, PROJECT_ROOT, load_settings
from batch_release.planner import READY, build_plan
from batch_release.report import load_ledger, summarize, write_report


# Resolve the Excel export, print a summary and write the plan workbook
def cmd_plan(args: argparse.Namespace) -> list:
    settings = load_settings(args.config)
    plan = build_plan(args.excel, settings, load_ledger(settings.results_dir), only_user=args.user)
    if args.rows:
        wanted = set(args.rows)
        plan = [r for r in plan if r.excel_row in wanted]
    path = write_report(settings.results_dir, plan, "plan")
    print(f"\nRows for users {', '.join(u.label for u in settings.users.values()) or '(all users)'}: {len(plan)}")
    for status, count in summarize(plan).items():
        print(f"  {status:<16} {count}")
    print(f"Plan written to: {path}\n")
    return plan


# Build the plan, then fill (and depending on mode, send) every READY request
def cmd_submit(args: argparse.Namespace) -> None:
    from batch_release.credentials import get_login
    from batch_release.portal import ConsolePrompts  # Playwright is only needed for this command
    from batch_release.runner import file_requests

    settings = load_settings(args.config)
    args.mode = args.mode or settings.default_mode
    profile = settings.users.get((args.user or "").upper())
    if not profile:
        sys.exit(f"submit needs --user, one of: {', '.join(settings.users)}")
    plan = cmd_plan(args)
    ready = [r for r in plan if r.status == READY]
    if args.limit:
        ready = ready[: args.limit]
    if not ready:
        print("Nothing is READY to file — open the plan workbook to see why.")
        return

    print(f"Mode: {args.mode}  —  {len(ready)} request(s) will be processed.")
    if args.mode == "auto" and input(">>> Type SEND to file them all without asking: ").strip() != "SEND":
        print("Cancelled.")
        return

    file_requests(
        settings, ready, args.mode, args.excel.name, ConsolePrompts(),
        on_start=lambda n, r: print(f"[{n}/{len(ready)}] row {r.excel_row}  batch {r.batch}  {r.product}"),
        on_done=lambda n, r: print(f"        -> {r.status}  {r.message}"),
        pause_s=args.pause, profile=profile, login=get_login(profile.sap_user),
    )

    path = write_report(settings.results_dir, plan, f"results_{args.mode}")
    print(f"\nDone. Results written to: {path}")


# Open the portal, let you log in, and dump the form's controls for selector calibration
def cmd_inspect(args: argparse.Namespace) -> None:
    from batch_release.portal import Portal

    settings = load_settings(args.config)
    with Portal(settings) as portal:
        page = portal._page()
        page.goto(settings.portal_url)
        input(">>> Log in, open the new-request form, then press Enter to capture its fields... ")
        out = portal.inspect_form(settings.results_dir / "form_controls.json")
        page.screenshot(path=str(settings.results_dir / "form_controls.png"), full_page=True)
    print(f"Form controls written to: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="File MOH batch-release requests from an SAP export.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, help_text in (("plan", cmd_plan, "check the Excel and folders, send nothing"),
                                ("submit", cmd_submit, "fill the portal form for every READY row")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("excel", type=Path, help="SAP export (.xlsx)")
        p.add_argument("--rows", type=int, nargs="*", help="only these Excel row numbers")
        p.add_argument("--user", help="SAP user filing (e.g. DSABAG01); only their rows are used")
        p.set_defaults(func=fn)
        if name == "submit":
            p.add_argument("--mode", choices=MODES, help="default: default_mode in config.yaml")
            p.add_argument("--limit", type=int, default=0, help="process at most N requests")
            p.add_argument("--pause", type=float, default=2.0, help="seconds between requests")

    p = sub.add_parser("inspect", help="capture the form's fields to calibrate selectors")
    p.set_defaults(func=cmd_inspect)

    args = parser.parse_args()
    if not args.config.exists():
        sys.exit(f"Config not found: {args.config}  (copy config.example.yaml to config.yaml)")
    args.func(args)


if __name__ == "__main__":
    main()
