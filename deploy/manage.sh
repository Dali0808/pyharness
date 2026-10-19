#!/usr/bin/env bash
# Single-host maintenance; run from any directory with bash. Never sources .env.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ -f .env ]] || { echo 'Copy .env.example to .env first' >&2; exit 1; }
exec 9>.maintenance.lock
flock -n 9 || { echo 'Another maintenance command is running' >&2; exit 1; }
compose=(docker compose --env-file .env)
[[ ! -f release.env ]] || compose+=(--env-file release.env)
dc() { "${compose[@]}" "$@"; }
health() {
    dc exec -T web python -c "import json,os,urllib.request; from urllib.parse import urlsplit; r=urllib.request.Request('http://127.0.0.1:8000/healthz',headers={'Host':urlsplit(os.environ['LARIO_PUBLIC_URL']).netloc}); d=json.load(urllib.request.urlopen(r,timeout=3)); assert d['status']=='ok' and d['frontend']; print(d['active_runs'])"
}
flag() {
    dc run --rm --no-deps -T --entrypoint python web -c "from pathlib import Path; p=Path('/data/maintenance.flag'); $1"
}
action=${1:-}
[[ $action == backup || $action == update ]] || { echo 'Usage: bash manage.sh backup | update IMAGE' >&2; exit 1; }
container=$(dc ps -q web)
[[ -n $container ]] || { echo 'Start Web before maintenance' >&2; exit 1; }
previous=$(docker inspect --format '{{.Config.Image}}' "$container")
next=${2:-}
if [[ $action == update ]]; then
    [[ $next =~ :sha-[a-f0-9]{40}$ || $next =~ @sha256:[a-f0-9]{64}$ ]] || { echo 'Use a sha-COMMIT or sha256 digest image reference' >&2; exit 1; }
    docker pull "$next"
fi
export LARIO_IMAGE=$previous
stopped=0
flagged=0
cleanup() {
    result=$?
    trap - EXIT
    if (( stopped )); then
        export LARIO_IMAGE=$previous
        dc up -d --no-deps web || result=1
    fi
    if (( flagged )); then
        flag 'p.unlink(missing_ok=True)' || result=1
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
flag 'p.touch()'
flagged=1
deadline=$((SECONDS + 300))
while true; do
    active=$(health)
    [[ $active != 0 ]] || break
    (( SECONDS < deadline )) || { echo 'Tasks still running; update aborted without stopping them' >&2; exit 1; }
    sleep 2
done
stopped=1
dc stop web
archive="/backups/lario-$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}.tar.gz"
dc run --rm --no-deps -T --entrypoint python web -m coding_agent.ops backup /data "$archive"
echo "Consistent backup: $archive (backups volume); copy it off this server"
if [[ $action == update ]]; then
    export LARIO_IMAGE=$next
fi
dc up -d --no-deps web
ready=0
for ((attempt=0; attempt<30; attempt++)); do
    if health >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
done
(( ready )) || { echo 'New version unhealthy; restarting previous image' >&2; exit 1; }
if [[ $action == update ]]; then
    umask 077
    printf 'LARIO_IMAGE=%s\n' "$next" > release.env.tmp
    mv -- release.env.tmp release.env
fi
stopped=0
flag 'p.unlink(missing_ok=True)'
flagged=0
echo "$action complete"
