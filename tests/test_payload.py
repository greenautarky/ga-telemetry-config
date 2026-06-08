"""Contract tests for the ga-telemetry-config payload.

We can't run telegraf or fluent-bit on a generic dev/CI machine without
a heavy install, so the tests focus on the contract that downstream
consumers (= the running system) actually depend on:

  - the boot-timing script is /bin/sh-syntax-valid and parses the
    monotonic-timestamp output it depends on
  - the fluent-bit tier0 config has the SERVICE + INPUT + OUTPUT
    sections that the downstream collector requires
  - the privacy classification (Tier-0) is documented in the config
    so a casual diff doesn't silently change consent scope
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = REPO_ROOT / "src" / "ga-telemetry-config"
BOOT_TIMING = PAYLOAD / "usr" / "libexec" / "ga-boot-timing"
FLUENT_BIT_T0 = PAYLOAD / "etc" / "fluent-bit" / "fluent-bit-tier0.conf"


def test_payload_files_exist_and_have_expected_modes():
    assert BOOT_TIMING.is_file(), f"missing: {BOOT_TIMING}"
    assert FLUENT_BIT_T0.is_file(), f"missing: {FLUENT_BIT_T0}"
    # ga-boot-timing must be executable; the tarball pipeline depends on
    # this mode being preserved (= telegraf's inputs.exec runs it).
    assert BOOT_TIMING.stat().st_mode & 0o111, (
        f"{BOOT_TIMING} is not executable — telegraf inputs.exec will fail"
    )


def test_boot_timing_is_sh_syntax_valid():
    """/bin/sh -n catches the kind of typo that wouldn't be caught
    until the device sends a single line of metrics into InfluxDB
    that nobody notices for a week."""
    result = subprocess.run(
        ["/bin/sh", "-n", str(BOOT_TIMING)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"sh -n failed: {result.stderr}"


@pytest.mark.parametrize(
    "expected_substring",
    [
        "systemctl show -p ActiveEnterTimestampMonotonic",
        "systemd-analyze",
        "boot_timing",  # the measurement name written into InfluxDB
    ],
)
def test_boot_timing_carries_downstream_contract(expected_substring):
    """If any of these go missing, the InfluxDB measurement breaks and
    the fleet dashboard panels go blank. Surface that here, not after
    a release."""
    content = BOOT_TIMING.read_text()
    assert expected_substring in content, (
        f"{expected_substring!r} not in ga-boot-timing — downstream contract drift"
    )


@pytest.mark.parametrize(
    "expected_section",
    ["[SERVICE]", "[INPUT]", "[OUTPUT]"],
)
def test_fluent_bit_tier0_has_required_sections(expected_section):
    """Fluent-bit silently no-ops missing sections. We assert presence."""
    content = FLUENT_BIT_T0.read_text()
    assert expected_section in content, (
        f"fluent-bit-tier0.conf missing section: {expected_section}"
    )


def test_fluent_bit_tier0_documents_privacy_classification():
    """Tier-0 = no-consent-required = DSGVO Art. 6 (b) Vertragserfüllung
    + (f) berechtigtes Interesse. A silent change of scope would be a
    real privacy + compliance issue; pin the documentation."""
    content = FLUENT_BIT_T0.read_text()
    assert "Tier-0" in content
    assert "PRIVACY_TIERS.md" in content, (
        "fluent-bit-tier0.conf must reference PRIVACY_TIERS.md so a "
        "casual diff sees the privacy classification"
    )
    # The scope MUST stay narrow (kernel panic / OOM / RAUC events,
    # failed auth, Supervisor failures). Anything wider would cross into
    # tier-1+ scope.
    for must_have in ("kernel panic", "OOM", "RAUC", "failed-auth", "Supervisor"):
        assert must_have in content, (
            f"fluent-bit-tier0.conf header missing scope marker {must_have!r}"
        )
