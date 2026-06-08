# ga-telemetry-config

GreenAutarky's on-device telemetry configuration, packaged as a Tier-2
OCI component so the telemetry schema can evolve independently of OS
releases.

## What's in here

| File | Purpose |
|---|---|
| `usr/libexec/ga-boot-timing` | Reads boot-related kernel/systemd timings and emits them as InfluxDB line-protocol points. Wired into telegraf as an `inputs.exec` block so each boot's metrics flow into the fleet's InfluxDB. |
| `etc/fluent-bit/fluent-bit-tier0.conf` | Fluent-bit "tier 0" config — captures the earliest boot logs (kernel/journald) and persists them to `/mnt/data/logs/` before fluent-bit's main config takes over. |

## What is **NOT** here (= why)

- `telegraf` binary install — buildroot package `package/telegraf/`
- `fluent-bit` binary install — buildroot package `package/fluent-bit-config/`
- `telegraf.conf` (main config) — currently still inside the buildroot
  package's source tree. Will graduate here in a future release once
  we've validated the override pattern.
- The telegraf + fluent-bit `.service` units — they live with the
  binary install in the buildroot package.

## Why a Tier-2 component

Telemetry schema evolves more often than the OS image. Each fleet
dashboard feature can bump `gaos_telemetry_*` measurements + tags
without forcing a full OS rebake. Same pattern as
`greenautarky-onboarding` and `ga-bootstrap`.

## Installation

Pulled at OS build time by the [ha-operating-system](https://github.com/greenautarky/ha-operating-system)
`scripts/sync-components.sh` based on the pin in `version.yaml`'s
`rootfs_overlays:` section.

The OCI artifact at `ghcr.io/greenautarky/ga-telemetry-config:<version>`
is a `.tar.gz` whose top-level layout matches the rootfs:

```
usr/libexec/ga-boot-timing
etc/fluent-bit/fluent-bit-tier0.conf
```

sync-components.sh extracts it directly into
`buildroot-external/rootfs-overlay/`, so the configs win over any
copies the buildroot packages may also install (rootfs-overlay applies
LATER in the buildroot build sequence).

## Releasing

1. Edit + bump `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. `git tag v1.0.X && git push origin v1.0.X`.
4. Release CI builds the tarball, pushes the OCI artifact, opens a
   GitHub Release.
5. In `ha-operating-system`, bump
   `version.yaml`'s `rootfs_overlays.ga-telemetry-config` pin.

## Testing

```bash
pip install -e .[dev]
pytest tests/
```

Tests assert the boot-timing script is syntactically valid (`/bin/sh -n`)
and that the fluent-bit config has the input + output sections we rely
on downstream (= a small contract test, NOT a full fluent-bit parse,
since fluent-bit may not be available on every dev machine).
