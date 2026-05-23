import json
from typing import Any, Dict, Iterable, Optional

import requests


class RoxyApiError(RuntimeError):
    pass


def load_config(path: str = "config.json") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _headers(config: Dict[str, Any]) -> Dict[str, str]:
    token = config.get("roxy_api_token", "")
    headers = {
        "Content-Type": "application/json",
    }
    if token:
        headers["token"] = token
        headers["Authorization"] = f"Bearer {token}"
    return headers


def roxy_request(
    config: Dict[str, Any],
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
    check_business_code: bool = True,
) -> Dict[str, Any]:
    base_url = config["roxy_base_url"].rstrip("/")
    resp = requests.request(
        method,
        f"{base_url}{path}",
        headers=_headers(config),
        params=params,
        json=json_body,
        timeout=timeout,
    )
    resp.raise_for_status()

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RoxyApiError(f"{path} did not return JSON: {resp.text[:300]}") from exc

    if check_business_code and payload.get("code") not in (0, "0", None):
        msg = payload.get("msg") or payload.get("message") or "unknown error"
        raise RoxyApiError(f"{path} failed: code={payload.get('code')}, msg={msg}")

    return payload


def workspace_rows(config: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    payload = roxy_request(config, "GET", "/browser/workspace")
    return payload.get("data", {}).get("rows", [])


def find_workspace_id(config: Dict[str, Any], profile_id: str) -> int:
    configured = config.get("roxy_workspace_id")
    if configured is not None:
        return int(configured)

    for workspace in workspace_rows(config):
        workspace_id = workspace.get("id")
        if workspace_id is None:
            continue

        payload = roxy_request(
            config,
            "GET",
            "/browser/list_v3",
            params={"workspaceId": workspace_id, "dirIds": profile_id, "page_size": 1},
            check_business_code=False,
        )
        if payload.get("code") in (0, "0"):
            rows = payload.get("data", {}).get("rows", [])
            if any(row.get("dirId") == profile_id for row in rows):
                return int(workspace_id)

    raise RoxyApiError(
        f"Profile {profile_id} was not found in any Roxy workspace. "
        "Check roxy_profile_id, or set roxy_workspace_id in config.json."
    )


def profile_summary(config: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
    workspace_id = find_workspace_id(config, profile_id)
    payload = roxy_request(
        config,
        "GET",
        "/browser/list_v3",
        params={"workspaceId": workspace_id, "dirIds": profile_id, "page_size": 1},
    )
    rows = payload.get("data", {}).get("rows", [])
    if not rows:
        raise RoxyApiError(f"Profile {profile_id} was not found in workspace {workspace_id}.")

    row = rows[0]
    return {
        "workspace_id": workspace_id,
        "profile_id": row.get("dirId"),
        "profile_name": row.get("windowName"),
        "workspace_name": row.get("workspaceName"),
    }


def launch_roxy_profile(
    profile_id: str,
    config: Optional[Dict[str, Any]] = None,
    args: Optional[list] = None,
) -> str:
    config = config or load_config()
    workspace_id = find_workspace_id(config, profile_id)
    payload = roxy_request(
        config,
        "POST",
        "/browser/open",
        json_body={"workspaceId": workspace_id, "dirId": profile_id, "args": args or []},
        timeout=60,
    )

    data = payload.get("data") or {}
    ws_endpoint = data.get("ws") or data.get("ws_endpoint")
    if not ws_endpoint:
        raise RoxyApiError(f"/browser/open succeeded but no websocket endpoint was returned: {payload}")

    return ws_endpoint
