#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import httpx

try:
    r = httpx.get("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", timeout=10)
    print(f"TLS OK, status={r.status_code}")
except Exception as exc:
    print(f"TLS FAIL: {exc!r}")
    raise SystemExit(1)
PY
