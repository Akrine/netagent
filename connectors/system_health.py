"""
connectors/system_health.py

Local system health connector using psutil.

Reads CPU, memory, disk, battery, network, and process data
directly from the host machine. No external API or credentials
required.

This connector serves two purposes:
  1. Proves the framework generalizes beyond Network Weather
  2. Provides a zero-dependency connector for local development
     and testing on any machine
"""

from __future__ import annotations

import platform
import socket
import time
from datetime import datetime, timezone
from typing import Optional

import psutil

from connectors.base import BaseConnector, ConnectorError
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    GatewayInfo,
    NetworkQuality,
    Severity,
    SystemHealth,
)


_THRESHOLDS = {
    "cpu_warning": 80.0,
    "cpu_critical": 95.0,
    "memory_warning": 80.0,
    "memory_critical": 95.0,
    "disk_warning": 85.0,
    "disk_critical": 95.0,
    "battery_warning": 20,
    "battery_critical": 10,
    "latency_warning_ms": 100.0,
    "latency_critical_ms": 300.0,
    "loss_warning_percent": 1.0,
    "loss_critical_percent": 5.0,
}


class SystemHealthConnector(BaseConnector):
    """
    Connector that reads live system health metrics from the local machine.

    The device_id parameter is ignored — this connector always reads
    from the machine it is running on. Pass any string (e.g. 'local').
    """

    def __init__(self, ping_target: str = "8.8.8.8") -> None:
        self._ping_target = ping_target

    @property
    def name(self) -> str:
        return "system_health"

    def health_check(self) -> bool:
        try:
            psutil.cpu_percent(interval=0.1)
            return True
        except Exception:
            return False

    def fetch(self, device_id: str = "local") -> DiagnosticSnapshot:
        try:
            return self._build_snapshot(device_id)
        except Exception as exc:
            raise ConnectorError(f"Failed to read system health: {exc}") from exc

    def _build_snapshot(self, device_id: str) -> DiagnosticSnapshot:
        captured_at = datetime.now(timezone.utc).isoformat()

        cpu = self._get_cpu()
        memory = self._get_memory()
        disk = self._get_disk()
        battery = self._get_battery()
        net_quality = self._get_network_quality()
        gateway = self._get_gateway()

        system = SystemHealth(
            cpu_percent=cpu["percent"],
            memory_percent=memory["percent"],
            disk_percent=disk["percent"],
            thermal_state=cpu.get("thermal_state", ""),
            uptime_seconds=self._get_uptime(),
            battery_percent=battery.get("percent"),
        )

        findings = self._generate_findings(
            cpu=cpu,
            memory=memory,
            disk=disk,
            battery=battery,
            net_quality=net_quality,
        )

        overall = self._compute_overall_severity(findings)

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=device_id,
            captured_at=captured_at,
            findings=findings,
            network_quality=net_quality,
            system=system,
            gateway=gateway,
            overall_severity=overall,
            raw={
                "cpu": cpu,
                "memory": memory,
                "disk": disk,
                "battery": battery,
                "platform": platform.platform(),
                "hostname": socket.gethostname(),
            },
        )

    def _get_cpu(self) -> dict:
        percent = psutil.cpu_percent(interval=1)
        count_logical = psutil.cpu_count(logical=True)
        count_physical = psutil.cpu_count(logical=False)
        freq = psutil.cpu_freq()
        result = {
            "percent": percent,
            "count_logical": count_logical,
            "count_physical": count_physical,
        }
        if freq:
            result["freq_mhz"] = round(freq.current, 1)
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                all_temps = [
                    t.current
                    for readings in temps.values()
                    for t in readings
                ]
                if all_temps:
                    result["temp_celsius"] = round(max(all_temps), 1)
                    if result["temp_celsius"] > 90:
                        result["thermal_state"] = "critical"
                    elif result["temp_celsius"] > 75:
                        result["thermal_state"] = "elevated"
                    else:
                        result["thermal_state"] = "nominal"
        except (AttributeError, NotImplementedError):
            pass
        return result

    def _get_memory(self) -> dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "percent": vm.percent,
            "used_gb": round(vm.used / 1024 ** 3, 1),
            "total_gb": round(vm.total / 1024 ** 3, 1),
            "available_gb": round(vm.available / 1024 ** 3, 1),
            "swap_used_gb": round(swap.used / 1024 ** 3, 1),
            "swap_total_gb": round(swap.total / 1024 ** 3, 1),
        }

    def _get_disk(self) -> dict:
        usage = psutil.disk_usage("/")
        io = psutil.disk_io_counters()
        result = {
            "percent": usage.percent,
            "used_gb": round(usage.used / 1024 ** 3, 1),
            "total_gb": round(usage.total / 1024 ** 3, 1),
            "free_gb": round(usage.free / 1024 ** 3, 1),
        }
        if io:
            result["read_mb"] = round(io.read_bytes / 1024 ** 2, 1)
            result["write_mb"] = round(io.write_bytes / 1024 ** 2, 1)
        return result

    def _get_battery(self) -> dict:
        battery = psutil.sensors_battery()
        if not battery:
            return {}
        return {
            "percent": int(battery.percent),
            "plugged_in": battery.power_plugged,
            "seconds_left": battery.secsleft
            if battery.secsleft != psutil.POWER_TIME_UNLIMITED
            else None,
        }

    def _get_network_quality(self) -> Optional[NetworkQuality]:
        try:
            import subprocess
            result = subprocess.run(
                ["ping", "-c", "4", "-W", "2000", self._ping_target],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout
            latency_ms = None
            loss_percent = 0.0

            for line in output.splitlines():
                if "min/avg/max" in line or "round-trip" in line:
                    parts = line.split("=")[-1].strip().split("/")
                    if len(parts) >= 2:
                        try:
                            latency_ms = float(parts[1])
                        except ValueError:
                            pass
                if "packet loss" in line:
                    for token in line.split():
                        if "%" in token:
                            try:
                                loss_percent = float(token.replace("%", ""))
                            except ValueError:
                                pass

            if latency_ms is not None:
                return NetworkQuality(
                    destination_latency_ms=latency_ms,
                    destination_loss_percent=loss_percent,
                )
        except Exception:
            pass
        return None

    def _get_gateway(self) -> Optional[GatewayInfo]:
        try:
            gateways = psutil.net_if_addrs()
            hostname = socket.gethostname()
            return GatewayInfo(
                vendor="Local Machine",
                model=platform.machine(),
                management_reachable=True,
                supports_integration=False,
                web_admin_url="",
            )
        except Exception:
            return None

    @staticmethod
    def _get_uptime() -> int:
        return int(time.time() - psutil.boot_time())

    def _generate_findings(
        self,
        cpu: dict,
        memory: dict,
        disk: dict,
        battery: dict,
        net_quality: Optional[NetworkQuality],
    ) -> list[Finding]:
        findings = []
        findings.extend(self._cpu_findings(cpu))
        findings.extend(self._memory_findings(memory))
        findings.extend(self._disk_findings(disk))
        findings.extend(self._battery_findings(battery))
        if net_quality:
            findings.extend(self._network_findings(net_quality))
        return findings

    def _cpu_findings(self, cpu: dict) -> list[Finding]:
        findings = []
        pct = cpu.get("percent", 0)
        if pct >= _THRESHOLDS["cpu_critical"]:
            findings.append(Finding(
                id="sys-cpu-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="CPU critically overloaded",
                description=f"CPU usage is at {pct:.1f}%, which will cause severe system slowdowns and application failures.",
                resolution="Identify and terminate the processes consuming the most CPU. Open Activity Monitor to find the culprits.",
                technical_detail=f"Usage: {pct:.1f}%, Cores: {cpu.get('count_logical')} logical / {cpu.get('count_physical')} physical",
            ))
        elif pct >= _THRESHOLDS["cpu_warning"]:
            findings.append(Finding(
                id="sys-cpu-warning",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="High CPU usage",
                description=f"CPU usage is at {pct:.1f}%, which may cause slowdowns during intensive tasks.",
                resolution="Check Activity Monitor for processes consuming high CPU. Consider closing unused applications.",
                technical_detail=f"Usage: {pct:.1f}%, Cores: {cpu.get('count_logical')} logical / {cpu.get('count_physical')} physical",
            ))
        temp = cpu.get("temp_celsius")
        if temp and temp > 90:
            findings.append(Finding(
                id="sys-thermal-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Critical CPU temperature",
                description=f"CPU temperature is {temp}C, which risks hardware damage and thermal throttling.",
                resolution="Ensure ventilation is not blocked. Clean cooling fans. Reduce CPU load immediately.",
                technical_detail=f"Temperature: {temp}C",
            ))
        elif temp and temp > 75:
            findings.append(Finding(
                id="sys-thermal-warning",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="Elevated CPU temperature",
                description=f"CPU temperature is {temp}C, which may cause thermal throttling.",
                resolution="Ensure the machine has adequate ventilation and is not running intensive tasks unnecessarily.",
                technical_detail=f"Temperature: {temp}C",
            ))
        return findings

    def _memory_findings(self, memory: dict) -> list[Finding]:
        findings = []
        pct = memory.get("percent", 0)
        if pct >= _THRESHOLDS["memory_critical"]:
            findings.append(Finding(
                id="sys-memory-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Critical memory pressure",
                description=f"Memory usage is at {pct:.1f}% ({memory.get('used_gb')} GB / {memory.get('total_gb')} GB). System is likely swapping heavily.",
                resolution="Close memory-intensive applications immediately. Restart the machine if swap usage is high.",
                technical_detail=f"Used: {memory.get('used_gb')} GB, Total: {memory.get('total_gb')} GB, Swap: {memory.get('swap_used_gb')} GB",
            ))
        elif pct >= _THRESHOLDS["memory_warning"]:
            findings.append(Finding(
                id="sys-memory-warning",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="High memory usage",
                description=f"Memory usage is at {pct:.1f}% ({memory.get('used_gb')} GB / {memory.get('total_gb')} GB).",
                resolution="Close unused browser tabs and applications to free memory.",
                technical_detail=f"Used: {memory.get('used_gb')} GB, Total: {memory.get('total_gb')} GB, Swap: {memory.get('swap_used_gb')} GB",
            ))
        return findings

    def _disk_findings(self, disk: dict) -> list[Finding]:
        findings = []
        pct = disk.get("percent", 0)
        if pct >= _THRESHOLDS["disk_critical"]:
            findings.append(Finding(
                id="sys-disk-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Disk critically full",
                description=f"Disk is {pct:.1f}% full ({disk.get('free_gb')} GB free). System may become unstable.",
                resolution="Delete large unused files immediately. Empty the trash. Move files to external storage.",
                technical_detail=f"Used: {disk.get('used_gb')} GB, Total: {disk.get('total_gb')} GB, Free: {disk.get('free_gb')} GB",
            ))
        elif pct >= _THRESHOLDS["disk_warning"]:
            findings.append(Finding(
                id="sys-disk-warning",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="Low disk space",
                description=f"Disk is {pct:.1f}% full with only {disk.get('free_gb')} GB remaining.",
                resolution="Review and delete large unused files. Consider archiving old data to external storage.",
                technical_detail=f"Used: {disk.get('used_gb')} GB, Total: {disk.get('total_gb')} GB, Free: {disk.get('free_gb')} GB",
            ))
        return findings

    def _battery_findings(self, battery: dict) -> list[Finding]:
        findings = []
        if not battery:
            return findings
        pct = battery.get("percent", 100)
        plugged = battery.get("plugged_in", True)
        if not plugged and pct <= _THRESHOLDS["battery_critical"]:
            findings.append(Finding(
                id="sys-battery-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Battery critically low",
                description=f"Battery is at {pct}% and not charging. Machine may shut down soon.",
                resolution="Connect to power immediately.",
                technical_detail=f"Battery: {pct}%, Plugged in: {plugged}",
            ))
        elif not plugged and pct <= _THRESHOLDS["battery_warning"]:
            findings.append(Finding(
                id="sys-battery-warning",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="Low battery",
                description=f"Battery is at {pct}% and not charging.",
                resolution="Connect to power when convenient.",
                technical_detail=f"Battery: {pct}%, Plugged in: {plugged}",
            ))
        return findings

    def _network_findings(self, nq: NetworkQuality) -> list[Finding]:
        findings = []
        latency = nq.destination_latency_ms
        loss = nq.destination_loss_percent

        if latency and latency >= _THRESHOLDS["latency_critical_ms"]:
            findings.append(Finding(
                id="sys-latency-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.CONNECTIVITY,
                title="Critical network latency",
                description=f"Network latency to {self._ping_target} is {latency:.1f} ms, which will severely impact real-time applications.",
                resolution="Check network connection, restart router, and contact ISP if the issue persists.",
                technical_detail=f"Latency: {latency:.1f} ms, Loss: {loss:.1f}%",
            ))
        elif latency and latency >= _THRESHOLDS["latency_warning_ms"]:
            findings.append(Finding(
                id="sys-latency-warning",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title="High network latency",
                description=f"Network latency to {self._ping_target} is {latency:.1f} ms, which may impact video calls and real-time applications.",
                resolution="Try restarting your router or moving closer to your WiFi access point.",
                technical_detail=f"Latency: {latency:.1f} ms, Loss: {loss:.1f}%",
            ))

        if loss and loss >= _THRESHOLDS["loss_critical_percent"]:
            findings.append(Finding(
                id="sys-loss-critical",
                severity=Severity.CRITICAL,
                category=FindingCategory.CONNECTIVITY,
                title="Critical packet loss",
                description=f"Packet loss to {self._ping_target} is {loss:.1f}%, which will cause severe disruptions to all network activity.",
                resolution="Restart your router and modem. Check all cable connections. Contact your ISP.",
                technical_detail=f"Loss: {loss:.1f}%, Latency: {latency:.1f} ms",
            ))
        elif loss and loss >= _THRESHOLDS["loss_warning_percent"]:
            findings.append(Finding(
                id="sys-loss-warning",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title="Elevated packet loss",
                description=f"Packet loss to {self._ping_target} is {loss:.1f}%, which may cause intermittent connectivity issues.",
                resolution="Restart your router. If on WiFi, move closer to the access point.",
                technical_detail=f"Loss: {loss:.1f}%, Latency: {latency:.1f} ms",
            ))

        return findings

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
