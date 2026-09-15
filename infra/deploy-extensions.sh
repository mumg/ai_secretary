#!/usr/bin/env bash
# Upload only extension sources/public Compose config; preserve server secrets and data.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
deploy_host="${DEPLOY_HOST:-192.168.9.108}"
deploy_user="${DEPLOY_USER:-maxim}"
deploy_dir="${DEPLOY_DIR:-/opt/secretary}"

# Values enter remote shell commands: accept only simple SSH/path components.
[[ "$deploy_host" =~ ^[a-zA-Z0-9.-]+$ ]] || exit 2
[[ "$deploy_user" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$deploy_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
remote="$deploy_user@$deploy_host"

python3 -m unittest discover -s "$repo_dir/extensions/tasks_tabs_new_services/tests" -v
docker compose -f "$repo_dir/compose.yaml" config --quiet

staging="$(ssh -o BatchMode=yes "$remote" 'mktemp -d /tmp/secretary-extensions.XXXXXXXX')"
[[ "$staging" =~ ^/tmp/secretary-extensions\.[a-zA-Z0-9]+$ ]] || exit 2
trap 'ssh -o BatchMode=yes "$remote" "rm -rf -- $staging"' EXIT

# Explicit allowlist; no dataPack, local secrets, backend edits or databases are uploaded.
COPYFILE_DISABLE=1 tar --no-xattrs -C "$repo_dir" \
  --exclude='__pycache__' --exclude='*.pyc' -cf - \
  extensions/Dockerfile extensions/.dockerignore \
  extensions/tasks_tabs_new_services/sync_tabs_tasks.py \
  extensions/tasks_tabs_new_services/run.py \
  extensions/tasks_tabs_new_services/config.example.json \
  extensions/tasks_tabs_new_services/environment.example.env \
  extensions/tasks_tabs_new_services/README.md \
  extensions/tasks_tabs_new_services/tests/test_tabs_tasks_script.py \
  compose.yaml infra/deploy-extensions.sh \
  | ssh -o BatchMode=yes "$remote" "tar -xf - -C $staging"

ssh -o BatchMode=yes "$remote" "sudo -n bash -s -- $deploy_dir $staging" <<'REMOTE'
set -euo pipefail
deploy_dir="$1"
staging="$2"
cd "$deploy_dir"
backup=".deploy/extensions-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -a compose.yaml "$backup/compose.yaml"
if [ -d extensions ]; then cp -a extensions "$backup/extensions"; fi
mkdir -p extensions infra
cp -a "$staging/extensions/." extensions/
cp "$staging/compose.yaml" compose.yaml
cp "$staging/infra/deploy-extensions.sh" infra/deploy-extensions.sh
if [ ! -d secrets/tasks_tabs_new_services ]; then
  install -d -m 750 -o 10001 -g 10001 secrets/tasks_tabs_new_services
fi
compose=(docker compose -f compose.yaml)
if [ -f compose.letsencrypt.yaml ]; then compose+=(-f compose.letsencrypt.yaml); fi
"${compose[@]}" config --quiet
"${compose[@]}" build extensions
"${compose[@]}" run --rm --no-deps -T extensions \
  python -m unittest discover -s tasks_tabs_new_services/tests -v < /dev/null
"${compose[@]}" up -d --no-deps extensions
"${compose[@]}" ps extensions
"${compose[@]}" logs --tail 10 extensions
REMOTE
