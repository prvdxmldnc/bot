#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import os
import uuid
import httpx

basic = os.getenv("GIGACHAT_BASIC_AUTH_KEY", "")
oauth_url = os.getenv("GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
verify = os.getenv("SSL_CERT_FILE") or True

if not basic:
    raise SystemExit("GIGACHAT_BASIC_AUTH_KEY is not set")

headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "RqUID": str(uuid.uuid4()),
    "Authorization": f"Basic {basic}",
}

with httpx.Client(verify=verify, timeout=10) as client:
    resp = client.post(oauth_url, headers=headers, data={"scope": scope})
    print("OAuth status=", resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise SystemExit("access_token missing")
    print("Access token prefix=", token[:10])
PY
