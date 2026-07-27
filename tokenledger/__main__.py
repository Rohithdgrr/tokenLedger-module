"""CLI tool for TokenLedger — inspect, export, verify, and compact usage data."""

import argparse
import json
import sys


def _build_ledger(args):
    from tokenledger import TokenLedger
    kwargs = {}
    if args.file:
        kwargs["persist_path"] = args.file
    return TokenLedger(**kwargs)


def cmd_summary(args):
    ledger = _build_ledger(args)
    records = ledger.get_records()
    if not records:
        print("No records found.")
        return
    summary = ledger.get_summary()
    print(f"Records:      {summary.get('total_requests', len(records))}")
    print(f"Input tokens: {summary.get('total_input_tokens', 0)}")
    print(f"Output tokens:{summary.get('total_output_tokens', 0)}")
    print(f"Total tokens: {summary.get('total_tokens', 0)}")
    print(f"Total cost:   ${summary.get('total_cost_usd', 0):.6f}")
    if args.detail:
        print("\nProviders:")
        for p in ledger.get_spending_by_provider():
            print(f"  {p['id']}: {p.get('total_tokens', 0)} tokens, ${p.get('cost_usd', 0):.6f}")


def cmd_export(args):
    ledger = _build_ledger(args)
    if args.format == "csv":
        ledger.export_csv(args.output)
        print(f"Exported CSV to {args.output}")
    elif args.format == "json":
        records = ledger.get_records()
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)
        print(f"Exported JSON to {args.output}")
    else:
        print(f"Unsupported format: {args.format}", file=sys.stderr)
        sys.exit(1)


def cmd_verify(args):
    ledger = _build_ledger(args)
    tampered = ledger.verify_immutability()
    if tampered:
        print(f"WARNING: {len(tampered)} tampered record(s) found:")
        for rid in tampered[:10]:
            print(f"  {rid}")
        if len(tampered) > 10:
            print(f"  ... and {len(tampered) - 10} more")
        sys.exit(1)
    records = ledger.get_records()
    print(f"All {len(records)} record(s) verified — checksums intact.")


def cmd_compact(args):
    ledger = _build_ledger(args)
    result = ledger.store.compact()
    print(f"Removed {result['removed']} record(s), {result['remaining']} remaining.")


def cmd_update_pricing(args):
    from tokenledger.core.pricing import PricingRegistry
    path = args.file
    if not path:
        import os
        builtin = os.path.join(os.path.dirname(__file__), "pricing_data.json")
        if os.path.exists(builtin):
            path = builtin
    if not path:
        print("No pricing file found. Use --file to specify one.", file=sys.stderr)
        sys.exit(1)
    pr = PricingRegistry(pricing_file=path)
    count = len(pr.list_models())
    updated = pr.get_last_updated() or "unknown"
    print(f"Loaded {count} model pricing entries from {path} (last updated: {updated})")

def cmd_health(args):
    ledger = _build_ledger(args)
    records = ledger.get_records()
    print("Status:       OK")
    print(f"Records:      {len(records)}")
    print(f"Persist path: {args.file or '(none — in-memory only)'}")
    tampered = ledger.verify_immutability()
    print(f"Integrity:    {'OK' if not tampered else f'{len(tampered)} tampered'}")
    print(f"Budgets:      {len(ledger.store.get_all_budgets())}")
    providers = set(r.get("provider", "?") for r in records)
    print(f"Providers:    {', '.join(sorted(providers)) if providers else '(none)'}")
    if records:
        first = min(r["timestamp"] for r in records)
        last = max(r["timestamp"] for r in records)
        print(f"Date range:   {first[:10]} to {last[:10]}")


def main():
    parser = argparse.ArgumentParser(prog="tokenledger", description="LLM usage tracking CLI")
    parser.add_argument("--file", "-f", help="Path to JSONL persist file")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

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
    p_pricing.add_argument("--file", "-f", help="Path to pricing JSON file (defaults to bundled data)")
    p_pricing.set_defaults(func=cmd_update_pricing)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
