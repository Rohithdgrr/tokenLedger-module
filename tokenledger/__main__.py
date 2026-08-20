"""CLI tool for TokenLedger — inspect, export, verify, and compact usage data."""

# mypy: disable-error-code="no-redef"
# The rich import below is optional; the fallback classes in the except
# branch intentionally reuse the same names.

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

try:  # rich is optional (install with `pip install tokenledger-module[cli]`)
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, TextColumn
    from rich.prompt import Prompt
    from rich.table import Table

    _HAS_RICH = True
except ImportError:  # pragma: no cover - exercised only when rich is not installed
    import re

    _HAS_RICH = False

    _TAG_RE = re.compile(r"(?:\[/?[a-zA-Z][a-zA-Z0-9._-]*\]|\[/\])")

    class _FallbackConsole:
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def print(self, *args: Any, **kwargs: Any) -> None:
            for a in args:
                print(_TAG_RE.sub("", str(a)))

    class _FallbackBox:
        SIMPLE = ""

    class _FallbackTable:
        def __init__(self, title: str = "", *args: Any, **kwargs: Any):
            self.title = title
            self._headers: list[str] = []
            self._rows: list[list[str]] = []

        def add_column(self, name: str, *args: Any, **kwargs: Any) -> None:
            self._headers.append(str(name))

        def add_row(self, *cells: Any) -> None:
            self._rows.append([str(c) for c in cells])

        def __str__(self) -> str:
            rows = [list(r) for r in self._rows]
            if self._headers:
                rows.insert(0, self._headers)
            if not rows:
                return self.title
            ncols = max(len(r) for r in rows)
            widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(ncols)]
            lines = ["  ".join(r[i].ljust(widths[i]) if i < len(r) else " " * widths[i] for i in range(ncols)) for r in rows]
            if self.title:
                lines.insert(0, _TAG_RE.sub("", self.title))
            return "\n".join(lines)

    class _FallbackPanel:
        @staticmethod
        def fit(text: Any, *args: Any, **kwargs: Any) -> str:
            return str(text)

    class _FallbackProgress:
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def __enter__(self) -> _FallbackProgress:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def add_task(self, description: str = "", *args: Any, **kwargs: Any) -> Any:
            if description:
                print(description)

    class _FallbackTextColumn:
        def __init__(self, *args: Any, **kwargs: Any):
            pass

    class _FallbackPrompt:
        @staticmethod
        def ask(question: str, default: Any = None, show_default: bool = True, choices: Any = None) -> str:
            text = question if choices is None else f"{question} ({', '.join(choices)})"
            answer = input(text + " ").strip()
            if not answer and default is not None:
                return str(default)
            return answer

    Console: Any = _FallbackConsole
    box: Any = _FallbackBox
    Table: Any = _FallbackTable
    Panel: Any = _FallbackPanel
    Progress: Any = _FallbackProgress
    TextColumn: Any = _FallbackTextColumn
    Prompt: Any = _FallbackPrompt

console = Console(highlight=False)

LIGHT_THEME = {
    "fg": "black",
    "accent": "blue",
    "success": "green",
    "error": "red",
    "muted": "bright_black",
    "border": "blue",
}


def _build_ledger(args: Any) -> Any:
    from tokenledger import TokenLedger
    kwargs: dict[str, Any] = {}
    sqlite_path = getattr(args, "sqlite", None)
    if sqlite_path:
        from tokenledger.ext.sqlite_store import SqliteStore

        kwargs["store"] = SqliteStore(sqlite_path)
    elif args.file:
        kwargs["persist_path"] = args.file
    key = getattr(args, "key", None)
    if key:
        kwargs["encryption_key"] = key
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

    table.add_row("Total Records", str(summary.get("requests", len(records))))
    table.add_row("Input Tokens", f"{summary.get('input_tokens', 0):,}")
    table.add_row("Output Tokens", f"{summary.get('output_tokens', 0):,}")
    table.add_row("Total Tokens", f"{summary.get('total_tokens', 0):,}")
    table.add_row("Total Cost", _format_cost(summary.get("cost_usd", 0)))
    table.add_row("Budgets", str(summary.get("budget_count", 0)))

    status_bd = summary.get("status_breakdown", {})
    if status_bd:
        non_ok = {k: v for k, v in status_bd.items() if k != "success"}
        if non_ok:
            table.add_row("Non-OK Requests", ", ".join(f"{k}={v}" for k, v in non_ok.items()))

    anomalies = summary.get("anomalies", {})
    if anomalies.get("non_success_count"):
        table.add_row("Anomalies", str(anomalies["non_success_count"]))

    console.print(table)

    top_models = summary.get("top_models", [])
    if top_models:
        mt = Table(title="Top Models", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
        mt.add_column("Model", style=LIGHT_THEME["accent"])
        mt.add_column("Tokens", style=LIGHT_THEME["fg"], justify="right")
        for m in top_models:
            mt.add_row(m["model"], f"{m['tokens']:,}")
        console.print(mt)

    top_providers = summary.get("top_providers", [])
    if top_providers:
        pt = Table(title="Top Providers", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
        pt.add_column("Provider", style=LIGHT_THEME["accent"])
        pt.add_column("Tokens", style=LIGHT_THEME["fg"], justify="right")
        for p in top_providers:
            pt.add_row(p["provider"], f"{p['tokens']:,}")
        console.print(pt)

    eff = ledger.get_efficiency()
    if eff.get("avg_efficiency"):
        et = Table(title="Efficiency", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
        et.add_column("Metric", style=LIGHT_THEME["accent"])
        et.add_column("Value", style=LIGHT_THEME["fg"])
        et.add_row("Avg Out/In Ratio", str(eff["avg_efficiency"]))
        et.add_row("Cache Hit Rate", str(eff["cache_hit_rate"]))
        et.add_row("Reasoning Tokens", f"{eff.get('total_reasoning_tokens', 0):,}")
        console.print(et)

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
    with Progress(TextColumn("[progress.description]{task.description}"), console=console) as progress:
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
    with Progress(TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task(description="Compacting...", total=None)
        result = ledger.store.compact()
    console.print(f"[{LIGHT_THEME['fg']}]Removed {result['removed']} record(s), {result['remaining']} remaining.[/]")


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
    persist = getattr(args, "sqlite", None) or args.file or "(in-memory only)"
    table.add_row("Persist Path", persist)
    table.add_row("Integrity", f"{'OK' if not tampered else f'{len(tampered)} tampered'}")
    table.add_row("Budgets", str(len(ledger.store.get_all_budgets())))
    providers = sorted({r.get("provider", "?") for r in records}) if records else ["(none)"]
    table.add_row("Providers", ", ".join(providers))
    if records:
        first = min(r["timestamp"] for r in records)[:10]
        last = max(r["timestamp"] for r in records)[:10]
        table.add_row("Date Range", f"{first} to {last}")
    console.print(table)


def cmd_cost(args: Any) -> None:
    ledger = _build_ledger(args)
    preview = ledger.cost_preview(
        [{"role": "user", "content": args.text}],
        args.model,
        args.provider,
        output_text=args.output_text,
    )
    table = Table(title="Cost Preview", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    table.add_column("Metric", style=LIGHT_THEME["accent"])
    table.add_column("Value", style=LIGHT_THEME["fg"])
    table.add_row("Provider", preview.get("provider", args.provider))
    table.add_row("Model", preview.get("model", args.model))
    table.add_row("Input Tokens", f"{preview.get('input_tokens', 0):,}")
    table.add_row("Output Tokens", f"{preview.get('output_tokens', 0):,}")
    table.add_row("Total Tokens", f"{preview.get('total_tokens', 0):,}")
    table.add_row("Estimated Cost", _format_cost(preview.get("cost_usd", 0.0)))
    table.add_row("Pricing Source", (preview.get("source") or "bundled pricing"))
    console.print(table)


COMMANDS: dict[str, Any] = {
    "summary": cmd_summary,
    "export": cmd_export,
    "verify": cmd_verify,
    "compact": cmd_compact,
    "health": cmd_health,
    "update-pricing": cmd_update_pricing,
    "cost": cmd_cost,
}


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


def _show_budgets(ledger: Any) -> None:
    budgets = ledger.store.get_all_budgets()
    if not budgets:
        console.print("[yellow]No budgets configured.[/]")
        return
    t = Table(title="Budgets", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    t.add_column("Scope", style=LIGHT_THEME["accent"])
    t.add_column("Scope ID", style=LIGHT_THEME["fg"])
    t.add_column("Limit", justify="right")
    t.add_column("Spent", justify="right")
    t.add_column("Util%", justify="right")
    for bk, b in budgets.items():
        # Window-aware spend (daily/weekly/monthly windows), not cumulative.
        util = ledger.analytics.get_budget_utilization(b.get("scope", "global"), b.get("scope_id", "all"))
        if util:
            spent = util["spent_usd"]
            limit = util["limit_usd"]
            pct = f"{util['utilization_percent']:.1f}"
        else:
            spent = 0.0
            limit = b.get("limit_usd", 0)
            pct = "N/A"
        t.add_row(b.get("scope", "?"), bk, _format_cost(limit), _format_cost(spent), pct)
    console.print(t)


def _show_efficiency(ledger: Any) -> None:
    eff = ledger.get_efficiency()
    t = Table(title="Efficiency", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    t.add_column("Metric", style=LIGHT_THEME["accent"])
    t.add_column("Value", style=LIGHT_THEME["fg"])
    t.add_row("Avg Out/In Ratio", str(eff.get("avg_efficiency", 0)))
    t.add_row("P50 Out/In Ratio", str(eff.get("p50_efficiency", 0)))
    t.add_row("Cache Hit Rate", str(eff.get("cache_hit_rate", 0)))
    t.add_row("Reasoning Tokens", f"{eff.get('total_reasoning_tokens', 0):,}")
    console.print(t)


def _show_dimension(ledger: Any, dim: str) -> None:
    data = ledger.get_spending_by_dimension(dim)
    if not data:
        console.print(f"[yellow]No {dim} data found.[/]")
        return
    t = Table(title=f"By {dim.title()}", box=box.SIMPLE, border_style=LIGHT_THEME["border"])
    t.add_column(dim.title(), style=LIGHT_THEME["accent"])
    t.add_column("Tokens", justify="right")
    t.add_column("Cost", justify="right")
    for d in data:
        t.add_row(d["id"], f"{d.get('total_tokens', 0):,}", _format_cost(d.get("cost_usd", 0)))
    console.print(t)


INTERACTIVE_HELP = """
[b]Interactive Mode[/b]
  [b]s[/b]  Summary (with top models, efficiency, status)
  [b]d[/b]  Detail (per-provider breakdown)
  [b]e[/b]  Export
  [b]v[/b]  Verify
  [b]c[/b]  Compact
  [b]h[/b]  Health
  [b]b[/b]  Budgets (with utilization)
  [b]f[/b]  Efficiency (ratios, cache, reasoning)
  [b]p[/b]  Pricing (all known models)
  [b]r[/b]  Records (last 20)
  [b]n[/b]  By Conversation
  [b]g[/b]  By Agent
  [b]t[/b]  By Tenant
  [b]?[/b]  Help
  [b]q[/b]  Quit
"""


def _interactive(args: Any) -> None:
    console.print(Panel.fit(INTERACTIVE_HELP, border_style=LIGHT_THEME["border"]))
    ledger = _build_ledger(args)

    while True:
        try:
            key = (
                Prompt.ask(
                    f"[{LIGHT_THEME['accent']}]tokenledger[/]",
                    default="",
                    show_default=False,
                )
                .strip()
                .lower()
            )
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
            "b": lambda: _show_budgets(ledger),
            "f": lambda: _show_efficiency(ledger),
            "p": lambda: _show_pricing(args),
            "r": lambda: _show_records(ledger),
            "n": lambda: _show_dimension(ledger, "conversation"),
            "g": lambda: _show_dimension(ledger, "agent"),
            "t": lambda: _show_dimension(ledger, "tenant"),
        }
        action = actions.get(key)
        if action:
            try:
                action()
            except Exception as e:  # noqa: BLE001 - interactive loop must survive
                console.print(f"[red]Command failed: {e}[/]")
        else:
            console.print(f"[yellow]Unknown key: {key}. Press ? for help.[/]")


def _export_interactive(args: Any, ledger: Any) -> None:
    fmt = Prompt.ask("Export format", choices=["csv", "json"], default="csv")
    path = Prompt.ask("Output file")
    args.format = fmt
    args.output = path
    cmd_export(args)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tokenledger", description="LLM usage tracking CLI")
    parser.add_argument("--file", "-f", help="Path to JSONL persist file")
    parser.add_argument("--sqlite", help="Path to SQLite database (uses SqliteStore backend)")
    parser.add_argument("--key", "-k", metavar="KEY", help="Encryption key for the persist file (AES)")
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

    p_cost = sub.add_parser("cost", help="Estimate tokens and cost without recording")
    p_cost.add_argument("text", help="Prompt text to estimate")
    p_cost.add_argument("--model", default="gpt-4o", help="Model name (default: gpt-4o)")
    p_cost.add_argument("--provider", default="openai", help="Provider name (default: openai)")
    p_cost.add_argument("--output-text", help="Optional completion text to estimate output tokens")
    p_cost.set_defaults(func=cmd_cost)

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
