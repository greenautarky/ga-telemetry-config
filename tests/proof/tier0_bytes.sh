#!/usr/bin/env bash
# tier0_bytes.sh — what one tier-0 day costs on the wire, per config.
#
# The REAL fluent-bit (3.2.10, the OS build) reads a fixture journal whose
# records carry the full field set a device's journald attaches (the 26 fields
# of a real tier-0 line: _BOOT_ID, _MACHINE_ID, _CAP_EFFECTIVE, _CMDLINE, ...),
# and pushes to an HTTP sink that records every Loki push body. Only the
# journal Path, the DB/storage paths and the Loki host/port are rewritten.
#
# Fixture mix = one device-day by class, from the 2026-10-09/10 Loki counts of
# a bench device (staging) scaled 1:20 to keep the run short:
#   PROBE  sshd connection bookkeeping of a port probe (4 lines per probe)
#   AUTH   accepted / failed logins
#   SESS   session bookkeeping of an accepted login
#   KERN   kernel errors
#   CONV   converge (ga_manager.jobs) lines
#
# Usage: tier0_bytes.sh <tier0 config> [<tier0 config> ...]
# Output per config: lines shipped, push body bytes, bytes per shipped line,
# and per class which fixture ids arrived.
set -uo pipefail

FB_IMAGE="${FLUENT_BIT_IMAGE:-fluent/fluent-bit@sha256:d6dec000c4929a439562525728c708f6e99800d7ddc82efd6aa4f45f3a20b562}"
JR="${JOURNAL_REMOTE:-/usr/lib/systemd/systemd-journal-remote}"
die2() { echo "ERROR (could not measure): $*" >&2; exit 2; }
[ $# -ge 1 ] || die2 "usage: $0 <config>..."
[ -x "$JR" ] || die2 "systemd-journal-remote not found (set JOURNAL_REMOTE)"
command -v docker >/dev/null || die2 "docker not on PATH"

W="$(mktemp -d "${TMPDIR:-/tmp}/t0bytes.XXXXXX")"; chmod 755 "$W"
cleanup() { docker rm -f t0b-fb >/dev/null 2>&1; [ -n "${SINK:-}" ] && kill "$SINK" 2>/dev/null; rm -rf "$W"; }
trap cleanup EXIT

BOOT=0f4c2a1e9b7d4c3a8e6f5d4c3b2a1908; MACH=3c9d1e7a5b2f4c6e8a0b1d3f5e7c9a2b; T0=1791446400000000; n=0
: > "$W/fx.export"
rec() {  # rec ID UNIT_OR_- COMM PRIORITY MESSAGE [extra FIELD=VALUE...]
  local id="$1" unit="$2" comm="$3" prio="$4" msg="$5"; shift 5; n=$((n + 1))
  {
    echo "__REALTIME_TIMESTAMP=$((T0 + n * 1000000))"; echo "__MONOTONIC_TIMESTAMP=$((n * 1000000))"
    echo "_BOOT_ID=$BOOT"; echo "_MACHINE_ID=$MACH"; echo "_HOSTNAME=kibu"; echo "_RUNTIME_SCOPE=system"
    echo "PRIORITY=$prio"; echo "SYSLOG_FACILITY=4"; echo "SYSLOG_IDENTIFIER=$comm"
    if [ "$unit" != - ]; then
      echo "_SYSTEMD_UNIT=$unit"; echo "_SYSTEMD_SLICE=system.slice"; echo "_SYSTEMD_CGROUP=/system.slice/$unit"
      echo "_SYSTEMD_INVOCATION_ID=5a1e2b3c4d5e6f708192a3b4c5d6e7f8"; echo "_STREAM_ID=8f7e6d5c4b3a29180f1e2d3c4b5a6978"
      echo "_TRANSPORT=syslog"; echo "_PID=$((2000 + n))"; echo "_UID=0"; echo "_GID=0"; echo "_COMM=$comm"
      echo "_EXE=/usr/sbin/$comm"; echo "_CMDLINE=sshd-session: [accepted]"; echo "_CAP_EFFECTIVE=1ffffffffff"
    else
      echo "_TRANSPORT=kernel"; echo "SYSLOG_IDENTIFIER=kernel"
    fi
    [ $# -gt 0 ] && printf '%s\n' "$@"
    echo "MESSAGE=FX-$id $msg"; echo
  } >> "$W/fx.export"
}
IP=100.64.12.34; ME=100.64.56.78
for i in $(seq 1 60); do   # 60 probes x 4 lines = 240 PROBE lines
  rec "PROBE$i-a" sshd.service sshd 6 "Connection from $IP port $((40000 + i)) on $ME port 22222 rdomain \"\""
  rec "PROBE$i-b" sshd.service sshd-session 6 "kex_exchange_identification: Connection closed by remote host"
  rec "PROBE$i-c" sshd.service sshd 6 "srclimit_penalise: ipv4: new $IP/32 deferred penalty of 1 seconds for penalty: connections without attempting authentication"
  rec "PROBE$i-d" sshd.service sshd-session 6 "Connection closed by $IP port $((40000 + i))"
done
for i in $(seq 1 6); do    # 6 logins: accepted (2 lines) + session bookkeeping (4 lines)
  rec "AUTH$i-a" sshd.service sshd-session 6 "Accepted certificate ID \"user@ga\" (serial 4242) signed by ECDSA CA SHA256:Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dQ via /etc/ssh/ga_user_ca.pub"
  rec "AUTH$i-b" sshd.service sshd-session 6 "Accepted publickey for root from $IP port $((50000 + i)) ssh2: ED25519-CERT SHA256:Zm9vYmFyYmF6cXV4 ID user@ga (serial 4242) CA ECDSA SHA256:YmFy"
  rec "SESS$i-a" sshd.service sshd-session 6 "Postponed publickey for root from $IP port $((50000 + i)) ssh2 [preauth]"
  rec "SESS$i-b" sshd.service sshd-session 6 "Starting session: command for root from $IP port $((50000 + i)) id 0"
  rec "SESS$i-c" sshd.service sshd-session 6 "Received disconnect from $IP port $((50000 + i)):11: disconnected by user"
  rec "SESS$i-d" sshd.service sshd-session 6 "Disconnected from user root $IP port $((50000 + i))"
done
for i in $(seq 1 4); do rec "FAIL$i" sshd.service sshd-session 6 "Failed publickey for root from $IP port $((60000 + i)) ssh2: RSA SHA256:cmV2b2tlZGtleQ"; done
for i in $(seq 1 2); do rec "KERN$i" - kernel 3 "rk_gmac-dwmac fe010000.ethernet eth0: __stmmac_open: Cannot attach to PHY (error: -19)"; done
for i in $(seq 1 2); do rec "CONV$i" docker.service addon_99f1cad4_ga_manager 3 "{\"ts\": \"2026-10-10T00:00:00Z\", \"level\": \"INFO\", \"logger\": \"ga_manager.jobs\", \"msg\": \"[job abc/converge] step 3/9 ok\"}" "CONTAINER_NAME=addon_99f1cad4_ga_manager"; done
mkdir -p "$W/journal"
"$JR" --output="$W/journal/fx.journal" "$W/fx.export" >/dev/null 2>&1 || die2 "journal-remote failed"

PORT=$((30000 + RANDOM % 20000))
python3 -I - "$PORT" "$W/sink" <<'PY' &
import http.server, sys, gzip, json, os
port, out = int(sys.argv[1]), sys.argv[2]; os.makedirs(out, exist_ok=True)
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); body = self.rfile.read(n)
        with open(os.path.join(out, 'pushes.bin'), 'ab') as f: f.write(len(body).to_bytes(8, 'big') + body)
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', port), H).serve_forever()
PY
SINK=$!; sleep 1

for cfg in "$@"; do
  name="$(basename "$cfg")"; d="$W/run-$name"; mkdir -p "$d/db" "$d/storage" "$d/etc"
  rm -f "$W/sink/pushes.bin"
  awk -v j=/w/journal '
    /^\[/ { sect=$0 } { print }
    sect=="[INPUT]" && $1=="Name" && $2=="systemd" { print "    Path                " j }' "$cfg" \
  | sed -E 's#/mnt/data/fluent-bit/db/#/w/db/#; s#(^[[:space:]]*Storage\.path[[:space:]]+).*#\1/w/storage#I; s#(^[[:space:]]*Flush[[:space:]]+).*#\11#' > "$d/etc/t0.conf"
  printf '[PARSER]\n    Name x\n    Format regex\n    Regex ^(?<m>.*)$\n' > "$d/etc/parsers.conf"
  cp -r "$W/journal" "$d/journal"; chmod -R a+rwX "$d"
  docker run -d --name t0b-fb --network host --user "$(id -u):$(id -g)" -v "$d:/w" -v "$d/etc:/fluent-bit/etc" \
    -e DEVICE_LABEL=KIB-SON-00000000 -e DEVICE_UUID=00000000000000000000000000000000 -e GA_ENV=staging \
    -e LOKI_HOST=127.0.0.1 -e LOKI_PORT="$PORT" -e LOKI_USER=x -e LOKI_PASSWORD=x -e LOKI_TENANT=x \
    "$FB_IMAGE" /fluent-bit/bin/fluent-bit -c /w/etc/t0.conf >/dev/null || die2 "fluent-bit start"
  sleep 8; docker stop -t 10 t0b-fb >/dev/null; docker rm t0b-fb >/dev/null
  [ -s "$W/sink/pushes.bin" ] || die2 "$name: no push reached the sink"
  python3 -I - "$W/sink/pushes.bin" "$name" <<'PY'
import sys, json, gzip, re, collections
data = open(sys.argv[1], 'rb').read(); i = 0; body_bytes = 0; lines = []; line_bytes = 0
while i < len(data):
    n = int.from_bytes(data[i:i+8], 'big'); b = data[i+8:i+8+n]; i += 8 + n; body_bytes += n
    if b[:2] == b'\x1f\x8b': b = gzip.decompress(b)
    for s in json.loads(b)['streams']:
        for ts, line in s['values']:
            lines.append(line); line_bytes += len(line.encode())
def klass(l):
    m = re.search(r'FX-(PROBE|AUTH|SESS|FAIL|KERN|CONV)', json.loads(l).get('MESSAGE', ''))
    return m.group(1) if m else 'other'
cls = collections.Counter(klass(l) for l in lines)
      f"bytes/line={line_bytes/max(1,len(lines)):.0f} push_bytes/line={body_bytes/max(1,len(lines)):.0f} classes={dict(sorted(cls.items()))}")
PY
done
