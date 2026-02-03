#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import os
import uuid
import httpx

basic = os.getenv("GIGACHAT_BASIC_AUTH_KEY", "")
oauth_url = os.getenv("GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
chat_base = os.getenv("GIGACHAT_API_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1")
scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
model = os.getenv("GIGACHAT_MODEL", "GigaChat")
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
    resp.raise_for_status()
    token = resp.json().get("access_token", "")
    if not token:
        raise SystemExit("access_token missing")

    chat_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Ответь одним словом: Да"}],
        "temperature": 0.2,
    }
    chat_resp = client.post(f"{chat_base}/chat/completions", headers=chat_headers, json=payload)
    print("Chat status=", chat_resp.status_code)
    chat_resp.raise_for_status()
    data = chat_resp.json()
    text = data["choices"][0]["message"]["content"]
    print(text.strip())
PY
