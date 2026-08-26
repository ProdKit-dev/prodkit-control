#!/usr/bin/env bash
set -euo pipefail

image="prodkit-control-ci:${GITHUB_SHA:-local}"
container="prodkit-control-ci-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker image rm -f "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --pull=false --tag "$image" .

configured_user="$(docker image inspect --format '{{.Config.User}}' "$image")"
if [[ "$configured_user" != "prodkit" ]]; then
  echo "container image must run as the non-root prodkit user; found: ${configured_user:-<empty>}" >&2
  exit 2
fi

container_id="$(docker run -d --name "$container" -p 127.0.0.1::8000 "$image")"
test -n "$container_id"
port="$(docker port "$container" 8000/tcp | awk -F: 'END{print $NF}')"
test -n "$port"

health=""
ready_code=""
for _ in $(seq 1 60); do
  health="$(curl --silent --show-error --fail "http://127.0.0.1:${port}/healthz" 2>/dev/null || true)"
  if [[ "$health" == *'"status":"ok"'* ]]; then
    ready_code="$(curl --silent --output /tmp/prodkit-ready-response.json --write-out '%{http_code}' \
      "http://127.0.0.1:${port}/readyz" || true)"
    break
  fi
  sleep 1
done

if [[ "$health" != *'"status":"ok"'* ]]; then
  docker logs "$container" >&2 || true
  echo "container did not become healthy" >&2
  exit 2
fi

# The default public image intentionally starts without development header authentication.
# Readiness must therefore fail closed until an operator supplies a production principal resolver
# (or explicitly enables the local-development resolver).
if [[ "$ready_code" != "503" ]]; then
  cat /tmp/prodkit-ready-response.json >&2 2>/dev/null || true
  echo "default container readiness must fail closed without authentication configuration; got $ready_code" >&2
  exit 2
fi

echo "container build/startup smoke passed; health=200 readiness=503 (fail-closed default)"
