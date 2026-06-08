# Security & privacy — ga-telemetry-config

## Privacy classification

This bundle ships only **Tier-0** telemetry config (= no consent required).
Tier-0 scope under GreenAutarky's privacy model:

- Kernel panic / OOM / page-allocation-failure events
- RAUC update events (CRA-required: track update success/failure)
- Failed-auth events on the device admin
- Supervisor service-failure events
- Boot-timing milestones (= ga-boot-timing's InfluxDB output)

All justified under DSGVO Art. 6 (b) Vertragserfüllung + (f)
berechtigtes Interesse. See [`ga-ihost-docs/PRIVACY_TIERS.md`](https://github.com/greenautarky/ga-ihost-docs/blob/main/PRIVACY_TIERS.md)
for the full tier model and consent boundaries.

**Anything wider than this list MUST move to tier-1 (or higher) and
ship via a different consent-gated component.** The test suite
(`tests/test_payload.py::test_fluent_bit_tier0_documents_privacy_classification`)
asserts that the tier-0 scope markers stay in the config so a silent
diff can't quietly expand scope.

## Threat model

| Threat | Defense |
|---|---|
| Customer-installed addon trying to exfiltrate tier-0 logs | Tier-0 buffer lives at `/mnt/data/logs/` (or wherever fluent-bit-tier0.conf points). Path is NOT under `/share/`, so addons cannot mount-read it. |
| Tier-0 backpressure starving tier-1 | Tier-0 + tier-1 have **separate** storage buffers (= explicit in tier0 config). Tier-0 panic-event stream can't block tier-1 throughput. |
| Silent expansion of tier-0 scope | Pinned in tests + the in-config docstring + this doc. CI fails if the scope markers disappear. |

## Not defended against

- Compromise of the bake server (the OS image carries this config; if
  bake server is owned, the tier classifications are too).
- Adversary on the device with root — they can read every log.
