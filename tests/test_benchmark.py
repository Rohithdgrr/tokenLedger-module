"""Benchmarks for TokenLedger core operations."""

import pytest

from tokenledger import TokenLedger


def test_benchmark_record_usage(benchmark):
    l = TokenLedger()
    benchmark(l.record_usage, "openai", "gpt-4o", 100, 50, user_id="alice", project_id="app")


def test_benchmark_bulk_records(benchmark):
    l = TokenLedger()
    benchmark(lambda: [l.record_usage("openai", "gpt-4o", 100, 50) for _ in range(100)])


def test_benchmark_fingerprint(benchmark):
    messages = [{"role": "user", "content": "Hello world"}] * 10
    benchmark(TokenLedger.fingerprint_prompt, messages)


def test_benchmark_get_summary(benchmark):
    l = TokenLedger()
    for _ in range(1000):
        l.record_usage("openai", "gpt-4o", 100, 50)
    benchmark(l.get_summary, "global", "all")


def test_benchmark_verify_immutability(benchmark):
    l = TokenLedger()
    for _ in range(500):
        l.record_usage("openai", "gpt-4o", 100, 50)
    benchmark(l.verify_immutability)
