#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import httpx

try:
    r = httpx.get("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", timeout=10)
    print("TLS OK, status=", r.status_code)
except Exception as e:  # noqa: BLE001
    msg = str(e)
    print("TLS FAIL:", msg)
    raise SystemExit(1)
PY
