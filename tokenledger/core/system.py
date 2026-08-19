"""In-memory system metrics collector. Optional dependency: psutil."""

import threading
from datetime import datetime, timezone
from typing import Any, Optional


def _cpu() -> dict[str, Any]:
    try:
        import psutil
        return {
            "percent": psutil.cpu_percent(interval=0.1),
            "count": psutil.cpu_count(),
            "freq": getattr(psutil.cpu_freq(), "current", 0) if psutil.cpu_freq() else 0,
        }
    except ImportError:
        return {"percent": 0, "count": 0, "freq": 0}


def _ram() -> dict[str, Any]:
    try:
        import psutil
        m = psutil.virtual_memory()
        return {"total": m.total, "available": m.available, "percent": m.percent, "used": m.used}
    except ImportError:
        return {"total": 0, "available": 0, "percent": 0, "used": 0}


def _disk() -> dict[str, Any]:
    try:
        import psutil
        result = {}
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                result[part.mountpoint] = {
                    "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent,
                    "fstype": part.fstype,
                }
            except PermissionError:
                continue
        return result
    except ImportError:
        return {}


def _storage() -> dict[str, Any]:
    return _disk()


def _network() -> dict[str, Any]:
    try:
        import psutil
        n = psutil.net_io_counters()
        return {"bytes_sent": n.bytes_sent, "bytes_recv": n.bytes_recv, "packets_sent": n.packets_sent,
                "packets_recv": n.packets_recv}
    except ImportError:
        return {"bytes_sent": 0, "bytes_recv": 0, "packets_sent": 0, "packets_recv": 0}


def _internet(timeout: float = 2.0) -> dict[str, Any]:
    hosts = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
    for host in hosts:
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, 53))
            s.close()
            return {"reachable": True, "latency_ms": 0.0, "host": host}
        except (socket.timeout, OSError):
            continue
    return {"reachable": False, "latency_ms": 0.0, "host": ""}


def _temperature() -> dict[str, Any]:
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if temps:
            result = {}
            for name, entries in temps.items():
                result[name] = [{"label": e.label or name, "current": e.current, "high": e.high,
                                  "critical": e.critical} for e in entries]
            return result
    except (ImportError, AttributeError):
        pass
    try:
        import subprocess
        out = subprocess.check_output(
            ["wmic", "/namespace:\\\\root\\wmi", "PATH", "MSAcpi_ThermalZoneTemperature",
             "get", "CurrentTemperature", "/value"],
            timeout=5,
        )
        val = out.decode().strip()
        if "CurrentTemperature" in val:
            temp_k = float(val.split("=")[1].strip())
            return {"cpu": {"current": round(temp_k - 273.15, 1)}}
    except Exception:
        pass
    return {}


def _gpu() -> dict[str, Any]:
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.DEVNULL,
        )
        result = {}
        for line in out.decode().strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                result[parts[0]] = {
                    "utilization_percent": float(parts[1]),
                    "memory_used_mb": float(parts[2]),
                    "memory_total_mb": float(parts[3]),
                    "temperature_c": float(parts[4]),
                }
        return result
    except Exception:
        return {}


def _power() -> dict[str, Any]:
    try:
        import psutil
        b = psutil.sensors_battery()
        if b:
            return {"percent": b.percent, "plugged": b.power_plugged, "secsleft": b.secsleft if b.secsleft != -1 else None}
    except (ImportError, AttributeError):
        pass
    return {"percent": 0, "plugged": True, "secsleft": None}


def _processor() -> dict[str, Any]:
    import platform
    try:
        import psutil
        freq = psutil.cpu_freq()
        return {
            "architecture": platform.machine(),
            "name": platform.processor(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "max_freq_mhz": getattr(freq, "max", 0) if freq else 0,
        }
    except ImportError:
        return {"architecture": platform.machine(), "name": platform.processor(),
                "cores_physical": 0, "cores_logical": 0, "max_freq_mhz": 0}


class SystemMonitor:
    """In-memory system metrics collector. Uses psutil if available, graceful fallback otherwise."""

    def __init__(self, collection_interval: float = 60.0):
        self.interval = collection_interval
        self.metrics: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._processor_info = _processor()

    def snapshot(self, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with self.lock:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cpu": _cpu(),
                "ram": _ram(),
                "disk": _disk(),
                "storage": _storage(),
                "network": _network(),
                "internet": _internet(),
                "temperature": _temperature(),
                "gpu": _gpu(),
                "power": _power(),
                "processor": dict(self._processor_info),
            }
            if context:
                record.update(context)
            self.metrics.append(record)
            return record

    def get_metrics(self, start: Optional[str] = None, end: Optional[str] = None) -> list[dict[str, Any]]:
        with self.lock:
            result = list(self.metrics)
        if start:
            result = [r for r in result if r.get("timestamp", "") >= start]
        if end:
            result = [r for r in result if r.get("timestamp", "") <= end]
        return result

    def get_summary(self, start: Optional[str] = None, end: Optional[str] = None) -> dict[str, Any]:
        records = self.get_metrics(start, end)
        if not records:
            return {"count": 0}
        cpu_vals = [r["cpu"]["percent"] for r in records]
        ram_vals = [r["ram"]["percent"] for r in records]
        return {
            "count": len(records),
            "cpu_avg_percent": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0,
            "cpu_max_percent": round(max(cpu_vals), 1) if cpu_vals else 0,
            "ram_avg_percent": round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else 0,
            "ram_max_percent": round(max(ram_vals), 1) if ram_vals else 0,
            "period_start": records[0].get("timestamp", ""),
            "period_end": records[-1].get("timestamp", ""),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._collect_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _collect_loop(self) -> None:
        while not self._stop.is_set():
            self.snapshot()
            self._stop.wait(self.interval)

    def clear(self) -> None:
        with self.lock:
            self.metrics.clear()
