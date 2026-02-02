#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import httpx

try:
    response = httpx.get("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", timeout=10)
    print(f"TLS OK, status={response.status_code}")
except Exception as exc:  # noqa: BLE001
    message = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in message:
        raise SystemExit(1)
    print(f"TLS error: {message}")
    raise SystemExit(1)
PY
