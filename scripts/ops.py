"""Local operational status. Reads credentials locally and never prints them."""

import json

import httpx

from app.services.observability import ops_token


def main():
    with httpx.Client(trust_env=False, timeout=15) as client:
        response = client.get(
            "http://127.0.0.1:8000/api/ops/status",
            headers={"Authorization": "Bearer " + ops_token()},
        )
    if response.status_code != 200:
        raise SystemExit(
            "Operational status unavailable; HTTP " + str(response.status_code)
        )
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
