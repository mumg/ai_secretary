#!/bin/sh
set -eu
cd "$(dirname "$0")"
APP_VERSION=$(tr -d '\r\n' < version)
export APP_VERSION
exec docker compose "$@" build api document-parser cert-init
