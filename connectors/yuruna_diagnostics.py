"""
connectors/yuruna_diagnostics.py

Connector that reads a Yuruna failure diagnostics file and produces
a DiagnosticSnapshot for agent reasoning.

The diagnostics file is written by Get-SystemDiagnostics when a
Yuruna test cycle fails. This connector is the bridge between
Yuruna's test framework and Savvy's reasoning layer — the first
step toward having a local LLM diagnose why a verification failed.

File format:
    # Yuruna failure diagnostics
    # VM        : <name>
    # Guest     : <guest-type>
    # SSH user  : <user>
    # Address   : <ip>
    # Mechanism : <key|password|console>
    # Exit code : <int>
    # Captured  : <iso8601>
    # ---
    <raw diagnostic output>
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from connectors.base import BaseConnector, ConnectorError
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    Severity,
    SystemHealth,
)


class YurunaDiagnosticsConnector(BaseConnector):
    """
    Reads a Yuruna diagnostics file and surfaces findings for
    agent reasoning.

    Usage:
        connector = YurunaDiagnosticsConnector("/path/to/diagnostics.txt")
        snapshot = connector.fetch("test-ubuntu-server-01")
    """

    def __init__(self, diagnostics_path: Union[str, Path, None] = None) -> None:
        self._path = Path(diagnostics_path) if diagnostics_path else Path("/dev/null")

    @property
    def name(self) -> str:
        return "yuruna_diagnostics"

    def health_check(self) -> bool:
        return self._path.exists()

    def fetch(self, device_id: str = "local") -> DiagnosticSnapshot:
        if not self._path.exists():
            raise ConnectorError(f"Diagnostics file not found: {self._path}")
        try:
            return self._parse(device_id)
        except Exception as exc:
            raise ConnectorError(f"Failed to parse diagnostics: {exc}") from exc

    def _parse(self, device_id: str) -> DiagnosticSnapshot:
        content = self._path.read_text(encoding="utf-8", errors="replace")
        header, body = self._split(content)

        vm_name = header.get("vm", device_id)
        guest = header.get("guest", "unknown")
        address = header.get("address", "")
        mechanism = header.get("mechanism", "unknown")
        exit_code = int(header.get("exit code", "0"))
        captured_at = header.get("captured", datetime.now(timezone.utc).isoformat())

        findings = []
        system = None

        # Exit code 127 = command not found in guest
        if exit_code == 127:
            findings.append(Finding(
                id="yuruna-diag-pwsh-missing",
                severity=Severity.WARNING,
                category=FindingCategory.SYSTEM,
                title="PowerShell not installed in guest",
                description=(
                    f"Get-SystemDiagnostics ran via {mechanism} but pwsh is not "
                    f"available in the guest ({guest}). Diagnostic output is incomplete."
                ),
                resolution="Install PowerShell in the guest image to enable full diagnostics.",
                technical_detail=f"Exit code: {exit_code}, Mechanism: {mechanism}, Address: {address}",
            ))

        # Exit code non-zero and not 127 = SSH/console failure
        elif exit_code != 0:
            findings.append(Finding(
                id="yuruna-diag-collection-failed",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title="Diagnostics collection failed",
                description=(
                    f"Get-SystemDiagnostics could not collect data from {vm_name} "
                    f"via {mechanism} (exit {exit_code})."
                ),
                resolution="Check SSH key configuration and guest network reachability.",
                technical_detail=f"Exit code: {exit_code}, Mechanism: {mechanism}, Address: {address}",
            ))

        # Parse body if we have real output (use exit code, not body text scan)
        if body.strip() and exit_code == 0:
            system = self._parse_system_health(body)
            findings.extend(self._parse_findings_from_body(body))

        # Always add a finding about the test failure itself
        findings.append(Finding(
            id="yuruna-test-failure",
            severity=Severity.CRITICAL,
            category=FindingCategory.SYSTEM,
            title=f"Yuruna test cycle failed on {guest}",
            description=(
                f"A Yuruna verification cycle failed on {vm_name}. "
                f"Diagnostics were captured at {captured_at}."
            ),
            resolution="Review the failure screenshots and OCR text in the same log folder.",
            technical_detail=f"VM: {vm_name}, Guest: {guest}, Address: {address}",
        ))

        overall = self._compute_severity(findings)

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=vm_name,
            captured_at=captured_at,
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={
                "header": header,
                "body": body,
                "path": str(self._path),
            },
        )

    @staticmethod
    def _split(content: str) -> tuple[dict, str]:
        """Split file into header dict and body string."""
        header = {}
        body_lines = []
        in_body = False

        for line in content.splitlines():
            if in_body:
                body_lines.append(line)
            elif line.strip() == "# ---":
                in_body = True
            elif line.startswith("#"):
                clean = line.lstrip("#").strip()
                if ":" in clean:
                    key, _, value = clean.partition(":")
                    header[key.strip().lower()] = value.strip()

        return header, "\n".join(body_lines)

    @staticmethod
    def _parse_system_health(body: str) -> Optional[SystemHealth]:
        """Extract system metrics from structured diagnostics body."""
        cpu = None
        memory = None
        disk = None

        for line in body.splitlines():
            line = line.strip()
            # Skip embedded script source lines (contain ⏎ character)
            if "⏎" in line or len(line) > 500:
                continue
            # Memory: "Available%: 27.6% used (1 - MemAvailable/MemTotal)"
            m = re.search(r"Available%:\s*([\d.]+)%\s*used", line)
            if m:
                memory = float(m.group(1))
            # Disk: "/dev/mapper/... 15G 11G 2.9G 79% /"
            m = re.search(r"\s(\d+)%\s+/$", line)
            if m:
                disk = float(m.group(1))
            # Load average: "Load  : 0.54 0.70 0.33"
            m = re.search(r"Load\s*:\s*([\d.]+)", line)
            if m:
                cpu = float(m.group(1)) * 100 / 4  # normalize load to rough %
            # Generic fallbacks
            m = re.search(r"cpu[_\s]?usage[:\s]+([\d.]+)\s*%", line, re.IGNORECASE)
            if m:
                cpu = float(m.group(1))

        if cpu is None and memory is None and disk is None:
            return None

        return SystemHealth(
            cpu_percent=round(cpu, 1) if cpu else None,
            memory_percent=round(memory, 1) if memory else None,
            disk_percent=round(disk, 1) if disk else None,
        )

    @staticmethod
    def _parse_findings_from_body(body: str) -> list[Finding]:
        """Extract findings from structured diagnostics body."""
        findings = []
        body_lower = body.lower()

        # Parse PROBLEMS DETECTED section — search from end of file
        # to avoid matching the embedded script source in the middle
        problems_match = re.search(
            r"PROBLEMS DETECTED\s*={0,}\s*\n(.*?)(?:Diagnostics complete|\Z)",
            body[-5000:], re.DOTALL | re.IGNORECASE
        )
        if problems_match:
            problems_text = problems_match.group(1).strip()
            problem_lines = re.findall(r"\d+\.\s+(.+)", problems_text)
            for i, problem in enumerate(problem_lines):
                problem = problem.strip()
                severity = Severity.CRITICAL if any(
                    kw in problem.upper() for kw in ["FAILED", "ERROR", "CRITICAL", "KUBE:"]
                ) else Severity.WARNING
                findings.append(Finding(
                    id=f"yuruna-problem-{i+1}",
                    severity=severity,
                    category=FindingCategory.SYSTEM,
                    title=f"Yuruna detected: {problem[:80]}",
                    description=problem,
                    resolution="Review the full diagnostics file for details.",
                    technical_detail=f"Detected in PROBLEMS DETECTED section",
                ))

        # kubelet failure
        if "kubelet.service: failed" in body_lower or "kubelet.service: referenced but unset" in body_lower:
            findings.append(Finding(
                id="yuruna-diag-kubelet-failed",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="kubelet service failed",
                description="The kubelet service failed or has misconfigured environment variables.",
                resolution="Check kubelet logs: journalctl -u kubelet. Verify kubeadm configuration.",
                technical_detail="kubelet.service: Failed with result 'exit-code'",
            ))

        if "oomkilled" in body_lower or "out of memory" in body_lower:
            findings.append(Finding(
                id="yuruna-diag-oom",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="OOM kill detected in guest",
                description="A process was killed due to out-of-memory condition in the guest.",
                resolution="Increase guest memory allocation or reduce workload memory requirements.",
                technical_detail="OOMKilled detected in diagnostics output",
            ))

        if "crashloopbackoff" in body_lower:
            findings.append(Finding(
                id="yuruna-diag-crashloop",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Pod in CrashLoopBackOff",
                description="A Kubernetes pod is repeatedly crashing in the guest.",
                resolution="Check pod logs with kubectl logs. Review deployment configuration.",
                technical_detail="CrashLoopBackOff detected in diagnostics output",
            ))

        if "502" in body or "bad gateway" in body_lower:
            findings.append(Finding(
                id="yuruna-diag-502",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title="502 Bad Gateway detected",
                description="A 502 error was detected, likely from a GitHub or external service rate limit.",
                resolution="Check squid cache for the failing URL. Ensure flannel.yml is cached.",
                technical_detail="502 Bad Gateway in diagnostics output",
            ))

        if "helm" in body_lower and ("failed" in body_lower or "empty" in body_lower):
            findings.append(Finding(
                id="yuruna-diag-helm-failed",
                severity=Severity.CRITICAL,
                category=FindingCategory.SYSTEM,
                title="Helm deployment issue detected",
                description="Helm may have failed silently. Resource output blocks are empty, which causes malformed pod configurations.",
                resolution="Run 'yuruna resources <project> <env>' to recapture resource outputs. Check Helm release status.",
                technical_detail="Empty componentsRegistry or resource block in resources.output.yml",
            ))

        return findings

    @staticmethod
    def _compute_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        return Severity.INFO
