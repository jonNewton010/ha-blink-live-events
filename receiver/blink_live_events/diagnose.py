"""Compare the authenticated Blink client identifiers without exposing them."""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote

from .blink_account import BlinkAccountError, AUTH_STATE_NAME, _oauth_token, _request, _json_object
from .state import read_json


def client_diagnostics(data_dir: Path) -> dict[str, object]:
    state = _oauth_token(read_json(data_dir / AUTH_STATE_NAME), data_dir)
    tier = state.get("tier")
    account_id = state.get("account_id")
    if not tier or not account_id:
        raise BlinkAccountError("diagnostics", "saved_account_details_missing")
    root = f"https://rest-{tier}.immedia-semi.com/api"
    headers = {"Authorization": f"Bearer {state['access_token']}"}
    status, _, response = _request("GET", f"{root}/v2/users/info", headers=headers)
    if status != 200:
        raise BlinkAccountError("diagnostic_account_info", f"http_{status}")
    info = _json_object(response, "diagnostic_account_info")
    status, home_headers, _ = _request(
        "GET", f"{root}/v3/accounts/{quote(str(account_id), safe='')}/homescreen", headers=headers
    )
    if status != 200:
        raise BlinkAccountError("diagnostic_homescreen", f"http_{status}")
    info_id = info.get("client_id")
    header_id = home_headers.get("Client-Id")
    return {
        "account_info_http": 200,
        "homescreen_http": status,
        "account_client_id_present": info_id is not None,
        "homescreen_client_id_present": bool(header_id),
        "account_client_id_is_positive_number": str(info_id).isdigit() and int(str(info_id)) > 0,
        "homescreen_client_id_is_positive_number": str(header_id).isdigit() and int(str(header_id)) > 0,
        "client_ids_match": str(info_id) == str(header_id) if info_id is not None and header_id else None,
        "saved_client_matches_homescreen": str(state.get("client_id")) == str(header_id) if header_id else None,
        "client_verification_required": bool(info.get("client_verification_required")),
        "account_verification_required": bool(info.get("account_verification_required") or info.get("phone_verification_required")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/blink-live-events"))
    args = parser.parse_args()
    try:
        print(json.dumps(client_diagnostics(args.data_dir), indent=2))
    except BlinkAccountError as error:
        print(f"Blink diagnostic: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
