#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT/visualisation"
export HOME=/tmp
export NPM_CONFIG_CACHE=/tmp/politica-stage14-2-npm-cache
npm ci --ignore-scripts --prefer-offline --fetch-retries=10 --fetch-retry-mintimeout=2000 --fetch-retry-maxtimeout=10000
npm test
npm run build:app
