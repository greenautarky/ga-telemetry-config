# Changelog

## 1.0.2 — 2026-07-29

Fixes the journald filter construction in `fluent-bit-tier0.conf`. Two
inputs combined a field filter with `PRIORITY` filters under
`Systemd_Filter_Type Or`, which does not mean "field AND priority" — it
ORs everything, so both inputs matched every journal entry at priority
0-3 regardless of unit. Because Docker's journald driver stamps all
container stderr as priority 3, the always-on tier-0 stream was
carrying the full container output of the device.

Measured on a canary before the change: of the records the kernel input
collected, ~98.5% came from containers and ~1.5% were actual kernel
messages.

### Changed
- `tier0.kernel` — one field filter (`_TRANSPORT=kernel`); severity now
  applied by a `grep` filter on `PRIORITY`, where it is meaningful.
- `tier0.supervisor` — filtered on `CONTAINER_NAME=hassio_supervisor`.
  The previous `_SYSTEMD_UNIT=hassio-supervisor.service` matched nothing
  at all: the Supervisor runs as a container and that unit does not
  appear in the journal. Severity now comes from the message text,
  since `PRIORITY` is 3 for all container output.
- `tier0.auth` — `dropbear.service` added. The image ships dropbear,
  not openssh, and has no sudo, so the two existing filters matched
  nothing and this input — the failed-authentication evidence required
  under CRA and Art. 32 — had never captured an event. The filters are
  deliberately alternatives and rely on the default `Or`; this is now
  documented in the file.
- Loki output — `device_uuid` promoted to a label. Devices without
  `/mnt/data/ga-device-label` were producing streams labelled only
  `unknown`, which cannot be attributed to a device afterwards.


## 1.0.0 — 2026-06-08

Initial release. Part of the Layer-2 → Tier-2 migration audit
(see [ha-operating-system memory `project_tier2_components`] +
`ga-ihost-docs/TIER-2-COMPONENTS.md`).

### Contents
- `usr/libexec/ga-boot-timing` — InfluxDB line-protocol exporter for
  boot timings (kernel boot, systemd phases, journal-first-line). Was
  in `ha-operating-system/buildroot-external/rootfs-overlay/`.
- `etc/fluent-bit/fluent-bit-tier0.conf` — tier-0 boot-log capture
  config. Was in the same overlay.

### Out of scope (= for later versions)
- `telegraf.conf` (main config) — currently still installed by the
  `package/telegraf/` buildroot make-file. Will migrate here once the
  rootfs-overlay-wins-over-package override is validated end-to-end.
- `fluent-bit.conf` (main config) — same story as telegraf.conf.
- The `.service` units — they remain bundled with the binary install
  in the buildroot package.

### Test coverage
- Boot-timing script: `/bin/sh -n` syntax check + the exec line that
  telegraf inputs.exec depends on.
- Fluent-bit config: presence of the `[INPUT]`, `[OUTPUT]` and tag-
  prefix conventions downstream consumers rely on.
