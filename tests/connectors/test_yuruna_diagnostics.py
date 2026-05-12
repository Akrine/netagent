"""
tests/connectors/test_yuruna_diagnostics.py

Unit tests for the Yuruna diagnostics connector.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from connectors.yuruna_diagnostics import YurunaDiagnosticsConnector
from core.schema import Severity


MINIMAL_FILE = """\
# Yuruna failure diagnostics
# VM        : test-ubuntu-server-01
# Guest     : guest.ubuntu.server
# SSH user  : yuser1
# Address   : 192.168.64.4
# Mechanism : key
# Exit code : 127
# Captured  : 2026-05-11T18:07:07Z
# ---
bash: line 1: pwsh: command not found
"""

RICH_FILE = """\
# Yuruna failure diagnostics
# VM        : test-ubuntu-server-02
# Guest     : guest.ubuntu.server
# SSH user  : yuser1
# Address   : 192.168.64.5
# Mechanism : key
# Exit code : 0
# Captured  : 2026-05-11T18:07:07Z
# ---
CPU Usage: 87.3%
Available%: 91.2% used (1 - MemAvailable/MemTotal)
Disk Usage: 45.0%
CrashLoopBackOff detected in pod website-deployment
"""

BAD_GATEWAY_FILE = """\
# Yuruna failure diagnostics
# VM        : test-ubuntu-server-03
# Guest     : guest.ubuntu.server
# SSH user  : yuser1
# Address   : 192.168.64.6
# Mechanism : console
# Exit code : 0
# Captured  : 2026-05-11T18:07:07Z
# ---
error: unable to read URL 'https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml', server reported 502 Bad Gateway
"""


@pytest.fixture
def minimal_file(tmp_path):
    f = tmp_path / "diagnostics.txt"
    f.write_text(MINIMAL_FILE)
    return f


@pytest.fixture
def rich_file(tmp_path):
    f = tmp_path / "diagnostics_rich.txt"
    f.write_text(RICH_FILE)
    return f


@pytest.fixture
def bad_gateway_file(tmp_path):
    f = tmp_path / "diagnostics_502.txt"
    f.write_text(BAD_GATEWAY_FILE)
    return f


class TestParsing:
    def test_parses_vm_name_from_header(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert snap.device_id == "test-ubuntu-server-01"

    def test_parses_captured_at(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert "2026-05-11" in snap.captured_at

    def test_source_connector_name(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert snap.source_connector == "yuruna_diagnostics"

    def test_raw_contains_header(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert "header" in snap.raw
        assert snap.raw["header"]["vm"] == "test-ubuntu-server-01"


class TestFindings:
    def test_pwsh_missing_generates_warning(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        titles = [f.title for f in snap.findings]
        assert any("PowerShell" in t for t in titles)

    def test_test_failure_finding_always_present(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        titles = [f.title for f in snap.findings]
        assert any("Yuruna test cycle failed" in t for t in titles)

    def test_overall_severity_is_critical(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert snap.overall_severity == Severity.CRITICAL

    def test_502_finding_detected(self, bad_gateway_file):
        snap = YurunaDiagnosticsConnector(bad_gateway_file).fetch()
        ids = [f.id for f in snap.findings]
        assert "yuruna-diag-502" in ids

    def test_crashloop_finding_detected(self, rich_file):
        snap = YurunaDiagnosticsConnector(rich_file).fetch()
        ids = [f.id for f in snap.findings]
        assert "yuruna-diag-crashloop" in ids


class TestSystemHealth:
    def test_parses_cpu_from_body(self, rich_file):
        snap = YurunaDiagnosticsConnector(rich_file).fetch()
        assert snap.system is not None
        assert snap.system.cpu_percent == pytest.approx(87.3)

    def test_parses_memory_from_body(self, rich_file):
        snap = YurunaDiagnosticsConnector(rich_file).fetch()
        assert snap.system.memory_percent == pytest.approx(91.2)

    def test_no_system_when_pwsh_missing(self, minimal_file):
        snap = YurunaDiagnosticsConnector(minimal_file).fetch()
        assert snap.system is None


class TestHealthCheck:
    def test_health_check_true_when_file_exists(self, minimal_file):
        assert YurunaDiagnosticsConnector(minimal_file).health_check() is True

    def test_health_check_false_when_file_missing(self, tmp_path):
        missing = tmp_path / "nonexistent.txt"
        assert YurunaDiagnosticsConnector(missing).health_check() is False
