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


# ── Loki auth (ADR-0003 Step 3) ──────────────────────────────────────────────
#
# Regression guard for 2026-07-10. tier-0 is the ONLY fluent-bit on a device
# without telemetry consent (tier-1 is consent-gated), so on most of the fleet
# it is the sole Loki writer. It shipped with `Port 3100` hard-coded and a
# shared literal `Tenant_ID greenautarky`, i.e. no per-device authentication.
# Flipping Loki's `auth_enabled: true` would have made those devices go dark.
#
# The fix that was believed to cover this actually patched
# `network-details.conf` in ha-operating-system — a scaffold no service loads.
# These tests assert on the file the tier-0 unit really runs.

_TIER0_CONF = REPO_ROOT / "src/ga-telemetry-config/etc/fluent-bit/fluent-bit-tier0.conf"


def _loki_output_block() -> list[str]:
    """The [OUTPUT] block whose Name is loki, as a list of lines."""
    lines = _TIER0_CONF.read_text().splitlines()
    blocks, cur = [], None
    for line in lines:
        if line.strip().startswith("[") and line.strip().endswith("]"):
            if cur is not None:
                blocks.append(cur)
            cur = [line] if line.strip() == "[OUTPUT]" else None
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    for b in blocks:
        if any(ln.split()[:2] == ["Name", "loki"] for ln in b if ln.split()):
            return b
    raise AssertionError("no [OUTPUT] block with `Name loki` in fluent-bit-tier0.conf")


@pytest.mark.parametrize("key", ["Host", "Port", "http_user", "http_passwd", "tenant_id"])
def test_tier0_loki_output_is_env_driven(key):
    """Every endpoint/credential field must come from the systemd environment.

    A literal here means the device cannot present a per-device credential —
    and once Loki enforces auth, its logs are silently dropped.
    """
    block = _loki_output_block()
    matching = [ln for ln in block if ln.split() and ln.split()[0] == key]
    assert matching, f"tier-0 loki OUTPUT is missing `{key}`"
    value = matching[0].split(maxsplit=1)[1].strip()
    assert value.startswith("${") and value.endswith("}"), (
        f"tier-0 loki OUTPUT `{key}` must be an env reference, got {value!r}"
    )


def test_tier0_loki_output_has_no_shared_static_tenant():
    """`Tenant_ID greenautarky` was a fleet-wide shared tenant — no isolation."""
    block = _loki_output_block()
    offenders = [
        ln.strip()
        for ln in block
        if ln.strip().lower().startswith("tenant_id ")
        and not ln.split(maxsplit=1)[1].strip().startswith("${")
    ]
    assert not offenders, f"static tenant in tier-0 loki OUTPUT: {offenders}"
