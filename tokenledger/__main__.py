"""CLI tool for TokenLedger — inspect, export, verify, and compact usage data."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

console = Console(highlight=False)

LIGHT_THEME = {
    "bg": "white",
    "fg": "black",
    "accent": "blue",
    "success": "green",
    "warn": "yellow",
    "error": "red",
    "muted": "bright_black",
    "border": "blue",
}


def _build_ledger(args: Any) -> Any:
    from tokenledger import TokenLedger
    kwargs = {}
    if args.file:
        kwargs["persist_path"] = args.file
    return TokenLedger(**kwargs)


def _format_cost(cost: float) -> str:
    if cost >= 0.01:
        return f"${cost:.4f}"
    if cost > 0:
        return f"${cost:.6f}"
    return "$0.00"


def cmd_summary(args: Any) -> None:
    ledger = _build_ledger(args)
    records = ledger.get_records()
    summary = ledger.get_summary()

    table = Table(title="Usage Summary", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    table.add_column("Metric", style=LIGHT_THEME["accent"])
    table.add_column("Value", style=LIGHT_THEME["fg"])

    if not records:
        table.add_row("Records", "0")
        console.print(table)
        return

    table.add_row("Total Records", str(summary.get("total_requests", len(records))))
    table.add_row("Input Tokens", f"{summary.get('total_input_tokens', 0):,}")
    table.add_row("Output Tokens", f"{summary.get('total_output_tokens', 0):,}")
    table.add_row("Total Tokens", f"{summary.get('total_tokens', 0):,}")
    table.add_row("Total Cost", _format_cost(summary.get("total_cost_usd", 0)))
    console.print(table)

    if args.detail:
        providers = ledger.get_spending_by_provider()
        if providers:
            pt = Table(title="Per-Provider Breakdown", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
            pt.add_column("Provider", style=LIGHT_THEME["accent"])
            pt.add_column("Tokens", style=LIGHT_THEME["fg"], justify="right")
            pt.add_column("Cost", style=LIGHT_THEME["fg"], justify="right")
            for p in providers:
                pt.add_row(
                    p["id"],
                    f"{p.get('total_tokens', 0):,}",
                    _format_cost(p.get("cost_usd", 0)),
                )
            console.print(pt)


def cmd_export(args: Any) -> None:
    ledger = _build_ledger(args)
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task(description="Exporting...", total=None)
        if args.format == "csv":
            ledger.export_csv(args.output)
        elif args.format == "json":
            records = ledger.get_records()
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, default=str)
    console.print(f"[{LIGHT_THEME['success']}]Exported {args.format.upper()} to {args.output}[/]")


def cmd_verify(args: Any) -> None:
    ledger = _build_ledger(args)
    tampered = ledger.verify_immutability()
    records = ledger.get_records()
    if tampered:
        table = Table(title="Verification Results", box=box.SIMPLE, border_style=LIGHT_THEME["error"])
        table.add_column("Status", style=LIGHT_THEME["error"])
        table.add_column("Details", style=LIGHT_THEME["fg"])
        table.add_row("FAILED", f"{len(tampered)} tampered record(s) found")
        for rid in tampered[:10]:
            table.add_row("", f"  {rid}")
        if len(tampered) > 10:
            table.add_row("", f"  ... and {len(tampered) - 10} more")
        console.print(table)
        sys.exit(1)
    console.print(f"[{LIGHT_THEME['success']}]All {len(records)} record(s) verified — checksums intact.[/]")


def cmd_compact(args: Any) -> None:
    ledger = _build_ledger(args)
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task(description="Compacting...", total=None)
        result = ledger.store.compact()
    console.print(
        f"[{LIGHT_THEME['fg']}]Removed {result['removed']} record(s), "
        f"{result['remaining']} remaining.[/]"
    )


def cmd_update_pricing(args: Any) -> None:
    from tokenledger.core.pricing import PricingRegistry
    path = getattr(args, "pricing_file", None) or getattr(args, "file", None)
    if not path:
        import os
        builtin = os.path.join(os.path.dirname(__file__), "pricing_data.json")
        if os.path.exists(builtin):
            path = builtin
    if not path:
        console.print("[red]No pricing file found. Use --pricing-file to specify one.[/]")
        sys.exit(1)
    pr = PricingRegistry(pricing_file=path)
    count = len(pr.list_models())
    updated = pr.get_last_updated() or "unknown"
    console.print(f"[{LIGHT_THEME['fg']}]Loaded {count} model pricing entries from {path} (last updated: {updated})[/]")


def cmd_health(args: Any) -> None:
    ledger = _build_ledger(args)
    records = ledger.get_records()
    tampered = ledger.verify_immutability()

    table = Table(title="Store Health", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    table.add_column("Check", style=LIGHT_THEME["accent"])
    table.add_column("Value", style=LIGHT_THEME["fg"])
    table.add_row("Status", "OK")
    table.add_row("Records", str(len(records)))
    table.add_row("Persist Path", args.file or "(in-memory only)")
    table.add_row("Integrity", f"{'OK' if not tampered else f'{len(tampered)} tampered'}")
    table.add_row("Budgets", str(len(ledger.store.get_all_budgets())))
    providers = sorted({r.get("provider", "?") for r in records}) if records else ["(none)"]
    table.add_row("Providers", ", ".join(providers))
    if records:
        first = min(r["timestamp"] for r in records)[:10]
        last = max(r["timestamp"] for r in records)[:10]
        table.add_row("Date Range", f"{first} to {last}")
    console.print(table)


COMMANDS: dict[str, Any] = {
    "summary": cmd_summary,
    "export": cmd_export,
    "verify": cmd_verify,
    "compact": cmd_compact,
    "health": cmd_health,
    "update-pricing": cmd_update_pricing,
}


def _shortcut_bar() -> Panel:
    shortcuts = [
        ("[b]s[/b]", "Summary"),
        ("[b]e[/b]", "Export"),
        ("[b]v[/b]", "Verify"),
        ("[b]c[/b]", "Compact"),
        ("[b]h[/b]", "Health"),
        ("[b]p[/b]", "Pricing"),
        ("[b]r[/b]", "Records"),
        ("[b]d[/b]", "Detail"),
        ("[b]q[/b]", "Quit"),
    ]
    cols = [f"  [{LIGHT_THEME['accent']}]{(k)}[/] {desc}" for k, desc in shortcuts]
    return Panel(
        Columns(cols, equal=True, align="center"),
        title="[bold]TokenLedger CLI[/bold]",
        border_style=LIGHT_THEME["border"],
        subtitle="Press a key or type a command",
    )


def _show_records(ledger: Any) -> None:
    records = ledger.get_records()
    if not records:
        console.print("[yellow]No records found.[/]")
        return
    table = Table(box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    table.add_column("#", style=LIGHT_THEME["muted"], justify="right")
    table.add_column("Provider", style=LIGHT_THEME["accent"])
    table.add_column("Model", style=LIGHT_THEME["fg"])
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Cost", justify="right")
    for i, r in enumerate(records[-20:], 1):
        table.add_row(
            str(i),
            r.get("provider", "?"),
            r.get("model", "?"),
            str(r.get("input_tokens", 0)),
            str(r.get("output_tokens", 0)),
            _format_cost(r.get("cost_usd", 0)),
        )
    console.print(table)


def _show_pricing(args: Any) -> None:
    from tokenledger.core.pricing import PricingRegistry
    pr = PricingRegistry()
    models = pr.list_models()
    providers: dict[str, list[str]] = {}
    for key in models:
        if key.startswith("_meta"):
            continue
        provider, model = key.split(":", 1)
        providers.setdefault(provider, []).append(model)
    table = Table(title="Known Models", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    table.add_column("Provider", style=LIGHT_THEME["accent"])
    table.add_column("Models", style=LIGHT_THEME["fg"])
    for prov, mods in sorted(providers.items()):
        table.add_row(prov, ", ".join(sorted(mods)))
    console.print(table)


def _show_detail_summary(ledger: Any) -> None:
    providers = ledger.get_spending_by_provider()
    if not providers:
        console.print("[yellow]No provider data.[/]")
        return
    pt = Table(box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    pt.add_column("Provider", style=LIGHT_THEME["accent"])
    pt.add_column("Tokens", justify="right")
    pt.add_column("Cost", justify="right")
    for p in providers:
        pt.add_row(p["id"], f"{p.get('total_tokens', 0):,}", _format_cost(p.get("cost_usd", 0)))
    console.print(pt)


INTERACTIVE_HELP = """
[b]Interactive Mode[/b]
  [b]s[/b]  Summary
  [b]d[/b]  Detail (per-provider)
  [b]e[/b]  Export
  [b]v[/b]  Verify
  [b]c[/b]  Compact
  [b]h[/b]  Health
  [b]p[/b]  Pricing (show all known models)
  [b]r[/b]  Records (last 20)
  [b]?[/b]  Help
  [b]q[/b]  Quit
"""


def _interactive(args: Any) -> None:
    console.print(Panel.fit(INTERACTIVE_HELP, border_style=LIGHT_THEME["border"]))
    ledger = _build_ledger(args)

    while True:
        try:
            key = Prompt.ask(
                f"[{LIGHT_THEME['accent']}]tokenledger[/]",
                default="",
                show_default=False,
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not key:
            continue
        if key == "q":
            break
        if key == "?":
            console.print(Panel.fit(INTERACTIVE_HELP, border_style=LIGHT_THEME["border"]))
            continue

        actions = {
            "s": lambda: cmd_summary(args),
            "d": lambda: _show_detail_summary(ledger),
            "e": lambda: _export_interactive(args, ledger),
            "v": lambda: cmd_verify(args),
            "c": lambda: cmd_compact(args),
            "h": lambda: cmd_health(args),
            "p": lambda: _show_pricing(args),
            "r": lambda: _show_records(ledger),
        }
        action = actions.get(key)
        if action:
            action()
        else:
            console.print(f"[yellow]Unknown key: {key}. Press ? for help.[/]")


def _export_interactive(args: Any, ledger: Any) -> None:
    fmt = Prompt.ask(
        "Export format", choices=["csv", "json"], default="csv"
    )
    path = Prompt.ask("Output file")
    args.format = fmt
    args.output = path
    cmd_export(args)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tokenledger", description="LLM usage tracking CLI")
    parser.add_argument("--file", "-f", help="Path to JSONL persist file")
    parser.add_argument("--interactive", "-i", action="store_true", help="Force interactive mode")

    sub = parser.add_subparsers(dest="command")
    sub.required = False

    p_summary = sub.add_parser("summary", help="Show aggregated usage summary")
    p_summary.add_argument("--detail", "-d", action="store_true", help="Show per-provider breakdown")
    p_summary.set_defaults(func=cmd_summary)

    p_export = sub.add_parser("export", help="Export records to CSV or JSON")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--output", "-o", required=True, help="Output file path")
    p_export.set_defaults(func=cmd_export)

    p_verify = sub.add_parser("verify", help="Verify record immutability")
    p_verify.set_defaults(func=cmd_verify)

    p_compact = sub.add_parser("compact", help="Force retention pruning")
    p_compact.set_defaults(func=cmd_compact)

    p_health = sub.add_parser("health", help="Show store health and stats")
    p_health.set_defaults(func=cmd_health)

    p_pricing = sub.add_parser("update-pricing", help="Reload pricing from external JSON file")
    p_pricing.add_argument("--pricing-file", help="Path to pricing JSON file (defaults to bundled data)")
    p_pricing.set_defaults(func=cmd_update_pricing)

    argv = sys.argv[1:]
    has_subcommand = any(a in argv for a in COMMANDS)

    if not has_subcommand or "-i" in argv or "--interactive" in argv:
        args, _ = parser.parse_known_args(argv)
        _interactive(args)
        return

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
