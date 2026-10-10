#!/usr/bin/env bash
# cursor_fsync.sh — how many fsyncs does tier-0 fluent-bit cost while idle?
#
# WHY
#   A bench device (BOSv1.5.0-rc6, 2026-10-10): the SD card took 32.8 GB/day while idle,
#   and two thirds of it went away when fluent-bit-tier0 was paused (E1: ext4
#   journal commits 10.3/s -> 0.69/s). in_systemd saves its cursor into its
#   SQLite DB after EVERY collect round, and a round runs on every journal
#   change, from ANY unit, even when none of the input's matches fired. With
#   the SQLite default (synchronous=FULL, rollback journal) each save is a
#   transaction with fsyncs. Five DB-backed inputs x every journal write.
#
# WHAT RUNS (the subject, not a model)
#   The REAL fluent-bit the image builds (3.2.10, pinned by digest) with the
#   given tier-0 config. Only what cannot run off-device is rewritten: the
#   journal Path is the host's live journal (read-only), DB and storage paths
#   move to a scratch dir, and the Loki OUTPUT becomes `null` (nothing is
#   printed or sent). A sidecar strace counts fsync/fdatasync of the
#   fluent-bit process over the window, while a writer emits journal lines at
#   a fixed rate, so the stimulus is the same for every config measured.
#
# USAGE
#   cursor_fsync.sh <config> [<config> ...]   # each measured concurrently
#   WINDOW=60 RATE=3 cursor_fsync.sh old.conf new.conf
# Output: one line per config: "<name> fsync=<n> fdatasync=<n> per_s=<x>".
# Exit 2 = could not measure (never a verdict).
set -uo pipefail

WINDOW="${WINDOW:-60}"
RATE="${RATE:-3}"
FB_IMAGE="${FLUENT_BIT_IMAGE:-fluent/fluent-bit@sha256:d6dec000c4929a439562525728c708f6e99800d7ddc82efd6aa4f45f3a20b562}"
STRACE_IMAGE="${STRACE_IMAGE:-local/strace:trixie}"
JOURNAL_DIR="${JOURNAL_DIR:-}"
if [ -z "$JOURNAL_DIR" ]; then
  for c in /var/log/journal /run/log/journal; do [ -d "$c" ] && { JOURNAL_DIR="$c"; break; }; done
fi

die2() { echo "ERROR (could not measure): $*" >&2; exit 2; }
[ $# -ge 1 ] || die2 "usage: $0 <config>..."
command -v docker >/dev/null || die2 "docker not on PATH"
command -v systemd-cat >/dev/null || die2 "systemd-cat not on PATH"
[ -n "$JOURNAL_DIR" ] && [ -d "$JOURNAL_DIR" ] || die2 "no journal directory found"

W="$(mktemp -d "${TMPDIR:-/tmp}/cursor-fsync.XXXXXX")"
names=()
cleanup() {
  for n in "${names[@]}"; do docker rm -f "cf-$n" >/dev/null 2>&1; done
  [ -n "${EMIT:-}" ] && kill "$EMIT" 2>/dev/null
  rm -rf "$W"
}
trap cleanup EXIT

i=0
for cfg in "$@"; do
  i=$((i + 1))
  n="c$i-$(basename "$cfg" .conf | tr -c 'A-Za-z0-9\n' '-')"
  names+=("$n")
  d="$W/$n"; mkdir -p "$d/etc" "$d/db" "$d/storage"
  inputs="$(grep -c '^\[INPUT\]' "$cfg")"
  dbs="$(grep -cE '^[[:space:]]*DB[[:space:]]' "$cfg")"
  # Rewrite: Path on every systemd input, DB + storage into scratch, Loki -> null.
  awk -v jd="/journal" '
    /^\[/ { sect=$0 }
    { print }
    sect=="[INPUT]" && $1=="Name" && $2=="systemd" { print "    Path                " jd }
  ' "$cfg" \
  | sed -E 's#(^[[:space:]]*DB[[:space:]]+)/mnt/data/fluent-bit/db/#\1/work/db/#;
            s#(^[[:space:]]*Storage\.path[[:space:]]+).*#\1/work/storage#I' \
  | awk '
      /^\[/ { if (o) { print "[OUTPUT]\n    Name null\n    Match *"; o=0 }
              if ($0=="[OUTPUT]") { o=1; next } }
      o { next }
      { print }
      END { if (o) print "[OUTPUT]\n    Name null\n    Match *" }
    ' > "$d/etc/fluent-bit.conf"
  [ "$(grep -c '/work/db/' "$d/etc/fluent-bit.conf")" = "$dbs" ] || die2 "$cfg: DB rewrite touched fewer than $dbs inputs"
  [ "$(grep -c '^    Path                /journal' "$d/etc/fluent-bit.conf")" -ge 1 ] || die2 "$cfg: no systemd input rewritten"
  printf '[PARSER]\n    Name dummy\n    Format regex\n    Regex ^(?<m>.*)$\n' > "$d/etc/parsers.conf"
  chmod -R a+rwx "$d"
  docker run -d --name "cf-$n" \
    -e DEVICE_LABEL=x -e DEVICE_UUID=x -e GA_ENV=x -e LOKI_HOST=x -e LOKI_PORT=1 \
    -e LOKI_USER=x -e LOKI_PASSWORD=x -e LOKI_TENANT=x \
    -v "$JOURNAL_DIR:/journal:ro" -v "$d:/work" -v "$d/etc:/fluent-bit/etc:ro" \
    "$FB_IMAGE" /fluent-bit/bin/fluent-bit -c /fluent-bit/etc/fluent-bit.conf >/dev/null \
    || die2 "cannot start fluent-bit for $cfg"
  echo "$n: $inputs inputs, $dbs with a cursor DB" >&2
done

# Let the initial catch-up (reading the existing journal) finish first.
sleep 15
for n in "${names[@]}"; do
  [ "$(docker inspect -f '{{.State.Running}}' "cf-$n")" = true ] \
    || { docker logs "cf-$n" 2>&1 | tail -5 >&2; die2 "fluent-bit for $n exited"; }
done

( while :; do echo "cursor-fsync probe line" | systemd-cat -t cursor-fsync-probe -p info; sleep "$(awk -v r="$RATE" 'BEGIN{print 1/r}')"; done ) &
EMIT=$!

for n in "${names[@]}"; do
  docker run -d --name "cf-$n-strace" --pid="container:cf-$n" --cap-add SYS_PTRACE \
    "$STRACE_IMAGE" timeout -s INT "$WINDOW" strace -f -c -e trace=fsync,fdatasync -p 1 >/dev/null \
    || die2 "cannot start strace for $n"
  names+=("$n-strace")
done
sleep $((WINDOW + 3))
kill "$EMIT" 2>/dev/null; EMIT=

rc=0
for n in "${names[@]}"; do
  case "$n" in *-strace) ;; *) continue ;; esac
  base="${n%-strace}"
  out="$(docker logs "cf-$n" 2>&1)"
  # strace prints no table at all when the traced calls never happened, so
  # "attached" is the proof that the zero is a measurement, not a miss.
  echo "$out" | grep -q 'Process 1 attached' || { echo "$out" >&2; die2 "strace never attached to $base"; }
  fs="$(echo "$out" | awk '$NF=="fsync"{print $4}')"; fs="${fs:-0}"
  fds="$(echo "$out" | awk '$NF=="fdatasync"{print $4}')"; fds="${fds:-0}"
  echo "$base fsync=$fs fdatasync=$fds per_s=$(awk -v a="$fs" -v b="$fds" -v w="$WINDOW" 'BEGIN{printf "%.2f", (a+b)/w}') window=${WINDOW}s rate=${RATE}/s"
done
exit $rc
