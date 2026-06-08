# Changelog

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
