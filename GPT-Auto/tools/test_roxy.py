from roxy_client import (
    RoxyApiError,
    launch_roxy_profile,
    load_config,
    profile_summary,
    roxy_request,
)

CONFIG = load_config()
PROFILE_ID = CONFIG["roxy_profile_id"]

print("正在检查 RoxyBrowser API...")

try:
    health = roxy_request(CONFIG, "GET", "/health")
    print("健康检查:", health.get("msg"), health.get("data", ""))

    summary = profile_summary(CONFIG, PROFILE_ID)
    print("Workspace ID:", summary["workspace_id"])
    print("Workspace 名称:", summary.get("workspace_name") or "-")
    print("Profile 名称:", summary.get("profile_name") or "-")

    print("正在打开 Profile...")
    ws_endpoint = launch_roxy_profile(PROFILE_ID, CONFIG)
    print("打开成功，CDP WebSocket:")
    print(ws_endpoint)
except RoxyApiError as exc:
    print("Roxy API 错误:", exc)
except Exception as exc:
    print("调用失败:", exc)
