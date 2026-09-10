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

import re
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


# --- journald filter construction -------------------------------------
#
# Regression guards for 1.0.2. Three inputs in this file were silently
# collecting the wrong thing for months; none of it was visible from
# reading the config, only from running it against a real journal.


def _input_blocks() -> dict[str, list[str]]:
    """Every [INPUT] block, keyed by its Tag."""
    blocks, cur = {}, None
    for line in _TIER0_CONF.read_text().splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = [] if s == "[INPUT]" else None
            if cur is not None:
                blocks[len(blocks)] = cur
        elif cur is not None:
            cur.append(line)
    out = {}
    for blk in blocks.values():
        tag = next((ln.split(maxsplit=1)[1].strip()
                    for ln in blk if ln.split() and ln.split()[0] == "Tag"), None)
        if tag:
            out[tag] = blk
    return out


def test_no_input_mixes_a_field_filter_with_priority_filters():
    """The `Systemd_Filter_Type Or` trap.

    fluent-bit's default filter type is Or and it applies to ALL filters
    of an input, not to field groups. Combining `_TRANSPORT=kernel` with
    `PRIORITY=0..3` therefore does not mean "kernel AND warn+" — it
    matches every journal entry at that priority, from any unit. Since
    Docker stamps all container stderr as priority 3, such an input
    collects the device's whole container output.

    Narrow by field in the INPUT, by priority in a grep FILTER.
    """
    offenders = []
    for tag, blk in _input_blocks().items():
        filters = [ln.split(maxsplit=1)[1].strip()
                   for ln in blk if ln.split() and ln.split()[0] == "Systemd_Filter"]
        has_priority = any(f.startswith("PRIORITY=") for f in filters)
        has_field = any(not f.startswith("PRIORITY=") for f in filters)
        if has_priority and has_field:
            offenders.append(tag)
    assert not offenders, (
        "inputs mixing a field filter with PRIORITY filters: "
        f"{offenders} — they will match the whole journal at that priority"
    )


def test_supervisor_input_targets_the_container_not_a_unit():
    """There is no `hassio-supervisor.service` in the journal — the
    Supervisor runs as a container, so a _SYSTEMD_UNIT filter matches
    nothing and the input goes silent."""
    blk = _input_blocks().get("tier0.supervisor")
    assert blk, "tier0.supervisor input missing"
    joined = "\n".join(blk)
    assert "CONTAINER_NAME=hassio_supervisor" in joined
    assert "_SYSTEMD_UNIT=hassio-supervisor.service" not in joined


def test_auth_input_covers_the_ssh_daemon_this_image_ships():
    """The image ships dropbear, not openssh, and has no sudo. Without
    dropbear this input — the failed-auth evidence for CRA / Art. 32 —
    captures nothing at all."""
    blk = _input_blocks().get("tier0.auth")
    assert blk, "tier0.auth input missing"
    assert "_SYSTEMD_UNIT=dropbear.service" in "\n".join(blk)


def test_tier0_loki_labels_carry_the_device_uuid():
    """device_label is `unknown` on devices that never got
    /mnt/data/ga-device-label. Without the uuid such a stream cannot be
    attributed to a device — which also makes an erasure request
    unanswerable for it."""
    block = _loki_output_block()
    labels = next((ln for ln in block if ln.split() and ln.split()[0] == "Labels"), "")
    assert "device_uuid=${DEVICE_UUID}" in labels, (
        f"tier-0 Loki labels lack device_uuid: {labels.strip()!r}"
    )


# --- Tier-0 converge stream: operational visibility + PII contract -----------
#
# converge was lifted into the always-on tier-0 because it is invisible in the
# cloud on a fresh device otherwise (tier-1 is consent-gated and consent is only
# granted during onboarding, exactly when converge matters most). We ship only
# the two operational loggers and EXCLUDE ga_manager.room_map, which carries
# user-chosen room names (personal data). These fixtures read the LIVE grep regex
# out of the config, so they rot the moment the scope is widened.

CONVERGE_MUST_SHIP = [
    '{"logger": "ga_manager.jobs", "msg": "[job x/converge] step 11 provision"}',
    '{"logger": "ga_manager.jobs", "msg": "[job x/converge] installing addon"}',
    '{"logger": "ga_manager.jobs", "level": "WARNING", "msg": "self-check FAILED"}',
    '{"logger": "ga_manager.workers.converge", "msg": "proxy trust: 2 trusted"}',
    '{"logger":"ga_manager.jobs","msg":"[job x/converge] step 3"}',
]

CONVERGE_MUST_NOT_SHIP = [
    '{"logger": "ga_manager.room_map", "msg": "area kitchen (Ramins Schlafzimmer)"}',
    '{"logger": "ga_manager.network.strategies.lte_dongle.strategy", "msg": "x"}',
    '{"logger": "ga_manager.zigbee_bridge", "msg": "joined device 0x00"}',
    '{"logger": "ga_manager.jobs.runner", "msg": "queue drained"}',
    '{"logger": "ga_manager", "msg": "boot"}',
]


def _tier0_grep_regex(match_tag):
    """Return the `Regex MESSAGE ...` of the tier-0 [FILTER] whose Match is
    match_tag, read from the LIVE config. Re-declaring the pattern here would
    test a copy and stay green while the real gate rots."""
    text = FLUENT_BIT_T0.read_text()
    for block in re.split(r"\n(?=\[)", text):
        if block.lstrip().startswith("[FILTER]") and re.search(
            rf"^\s*Match\s+{re.escape(match_tag)}\s*$", block, re.M
        ):
            m = re.search(r"^\s*Regex\s+MESSAGE\s+(.+?)\s*$", block, re.M)
            if m:
                return m.group(1)
    return None


def test_tier0_converge_input_present():
    text = FLUENT_BIT_T0.read_text()
    assert "Tag                 tier0.converge" in text, "tier0.converge input missing"
    assert "CONTAINER_NAME=addon_99f1cad4_ga_manager" in text, (
        "converge input must filter on the ga_manager container"
    )


def test_tier0_converge_grep_ships_operational_and_drops_pii():
    pat = _tier0_grep_regex("tier0.converge")
    assert pat is not None, (
        "tier0.converge grep Regex not found in the live config -- fail closed "
        "rather than pass on an empty check"
    )
    rx = re.compile(pat)
    for line in CONVERGE_MUST_SHIP:
        assert rx.search(line), f"operational converge line was dropped: {line}"
    for line in CONVERGE_MUST_NOT_SHIP:
        assert not rx.search(line), f"line must NOT ship (PII / out of scope): {line}"
