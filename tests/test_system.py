"""Tests for SystemMonitor — metrics, edge cases, integration."""

import time
import threading
from datetime import datetime, timezone

import pytest

from tokenledger import TokenLedger
from tokenledger.core.system import SystemMonitor


class TestSystemMonitor:
    def test_snapshot_structure(self):
        m = SystemMonitor()
        snap = m.snapshot()
        assert "timestamp" in snap
        assert "cpu" in snap
        assert "ram" in snap
        assert "disk" in snap
        assert "storage" in snap
        assert "network" in snap
        assert "internet" in snap
        assert "temperature" in snap
        assert "gpu" in snap
        assert "power" in snap
        assert "processor" in snap
        # cpu sub-fields
        assert "percent" in snap["cpu"]
        assert "count" in snap["cpu"]
        # ram sub-fields
        assert "total" in snap["ram"]
        assert "percent" in snap["ram"]
        # processor sub-fields
        assert "architecture" in snap["processor"]
        assert "name" in snap["processor"]

    def test_snapshot_with_context(self):
        m = SystemMonitor()
        snap = m.snapshot({"tag": "test-call", "model": "gpt-4o"})
        assert snap["tag"] == "test-call"
        assert snap["model"] == "gpt-4o"
        assert "timestamp" in snap

    def test_get_metrics_default(self):
        m = SystemMonitor()
        assert m.get_metrics() == []
        m.snapshot()
        assert len(m.get_metrics()) == 1
        m.snapshot()
        assert len(m.get_metrics()) == 2

    def test_get_metrics_time_range(self):
        m = SystemMonitor()
        m.snapshot()
        t1 = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        m.snapshot()
        t2 = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        m.snapshot()
        assert len(m.get_metrics(start=t1)) == 2  # t1 and after
        assert len(m.get_metrics(end=t2)) == 2    # t2 and before
        assert len(m.get_metrics(start=t1, end=t2)) == 1  # between t1 and t2

    def test_get_summary_empty(self):
        m = SystemMonitor()
        s = m.get_summary()
        assert s["count"] == 0

    def test_get_summary_with_data(self):
        m = SystemMonitor()
        m.snapshot()
        m.snapshot()
        s = m.get_summary()
        assert s["count"] == 2
        assert "cpu_avg_percent" in s
        assert "ram_avg_percent" in s
        assert "period_start" in s
        assert "period_end" in s

    def test_start_stop(self):
        m = SystemMonitor(collection_interval=0.1)
        m.snapshot()  # warm-up snapshot (GPU/internet are slow)
        count_before = len(m.get_metrics())
        m.start()
        time.sleep(0.6)
        m.stop()
        count_after = len(m.get_metrics())
        assert count_after > count_before  # should have added more snapshots

    def test_start_idempotent(self):
        m = SystemMonitor(collection_interval=0.05)
        m.start()
        t1 = threading.active_count()
        m.start()  # second start should be no-op
        t2 = threading.active_count()
        assert t1 == t2  # no extra thread
        m.stop()

    def test_stop_without_start(self):
        m = SystemMonitor()
        m.stop()  # should not raise

    def test_clear(self):
        m = SystemMonitor()
        m.snapshot()
        m.snapshot()
        assert len(m.get_metrics()) == 2
        m.clear()
        assert len(m.get_metrics()) == 0

    def test_processor_info(self):
        m = SystemMonitor()
        snap = m.snapshot()
        proc = snap["processor"]
        assert isinstance(proc["architecture"], str)
        assert isinstance(proc["name"], str)

    def test_concurrent_snapshot(self):
        m = SystemMonitor()
        errors = []

        def shoot():
            try:
                for _ in range(20):
                    m.snapshot()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=shoot) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(m.get_metrics()) == 80


class TestSystemIntegration:
    def test_record_without_system_monitor(self):
        l = TokenLedger()
        r = l.record_usage("test", "m", 10, 5)
        assert "system" not in r

    def test_record_with_system_monitor(self):
        m = SystemMonitor()
        l = TokenLedger(system_monitor=m)
        r = l.record_usage("test", "m", 10, 5, system_context=True)
        assert "system" in r
        assert "cpu" in r["system"]
        assert "ram" in r["system"]

    def test_record_system_context_without_monitor_raises(self):
        l = TokenLedger()
        with pytest.raises(ValueError, match="system_context=True requires"):
            l.record_usage("test", "m", 10, 5, system_context=True)

    def test_system_context_attached_to_interceptor_records(self):
        m = SystemMonitor()
        l = TokenLedger(system_monitor=m)
        r = l.record_usage("test", "m", 10, 5, system_context=True)
        assert r["system"]["cpu"]["percent"] >= 0
