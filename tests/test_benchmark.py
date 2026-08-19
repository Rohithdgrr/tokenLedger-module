"""Benchmarks for TokenLedger core operations."""


from tokenledger import TokenLedger


def test_benchmark_record_usage(benchmark):
    ledger = TokenLedger()
    benchmark(ledger.record_usage, "openai", "gpt-4o", 100, 50, user_id="alice", project_id="app")


def test_benchmark_bulk_records(benchmark):
    ledger = TokenLedger()
    benchmark(lambda: [ledger.record_usage("openai", "gpt-4o", 100, 50) for _ in range(100)])


def test_benchmark_fingerprint(benchmark):
    messages = [{"role": "user", "content": "Hello world"}] * 10
    benchmark(TokenLedger.fingerprint_prompt, messages)


def test_benchmark_get_summary(benchmark):
    ledger = TokenLedger()
    for _ in range(1000):
        ledger.record_usage("openai", "gpt-4o", 100, 50)
    benchmark(ledger.get_summary, "global", "all")


def test_benchmark_verify_immutability(benchmark):
    ledger = TokenLedger()
    for _ in range(500):
        ledger.record_usage("openai", "gpt-4o", 100, 50)
    benchmark(ledger.verify_immutability)
