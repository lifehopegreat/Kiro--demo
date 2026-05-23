import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests


def _provider_config(config):
    provider = dict((config.get("verification_code_bridge") or {}))
    provider.setdefault("mode", "file")
    provider.setdefault("file", "codes.json")
    provider.setdefault("url", "http://127.0.0.1:8787/code")
    provider.setdefault("poll_interval", 2)
    provider.setdefault("consume", True)
    provider.setdefault("http_timeout", 10)
    return provider


def _extract_code(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("code", "value", "otp", "verification_code"):
            code = _extract_code(value.get(key))
            if code:
                return code
        return ""
    if isinstance(value, (int, float)):
        value = str(int(value))
    text = str(value).strip()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else ""


def _load_json_file(path):
    file_path = Path(path)
    if not file_path.exists():
        return {}
    text = file_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _save_json_file(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_code_in_mapping(data, email_address):
    if not isinstance(data, dict):
        return ""
    candidates = [
        email_address,
        email_address.lower(),
        email_address.upper(),
    ]
    for key in candidates:
        if key in data:
            code = _extract_code(data.get(key))
            if code:
                return code
    for key, value in data.items():
        if str(key).lower() == email_address.lower():
            code = _extract_code(value)
            if code:
                return code
    return ""


def get_code_from_file(email_address, config):
    provider = _provider_config(config)
    data = _load_json_file(provider["file"])
    code = _find_code_in_mapping(data, email_address)
    if code and provider.get("consume", True):
        for key in list(data.keys()):
            if str(key).lower() == email_address.lower():
                data.pop(key, None)
        _save_json_file(provider["file"], data)
    return code


def get_code_from_http(email_address, config):
    provider = _provider_config(config)
    base_url = provider["url"]
    query = urlencode({"email": email_address})
    url = f"{base_url}?{query}" if "?" not in base_url else f"{base_url}&{query}"
    response = requests.get(url, timeout=provider.get("http_timeout", 10))
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "application/json" in content_type:
        data = response.json()
        if isinstance(data, dict):
            code = _extract_code(data)
            if code:
                return code
            return _find_code_in_mapping(data, email_address)
    return _extract_code(response.text)


def get_code_from_outlook_graph(email_address, config):
    """通过 Microsoft Graph API 获取验证码"""
    from outlook_receiver import get_code_from_outlook
    return get_code_from_outlook(email_address, config)


def get_code_once(email_address, config):
    provider = _provider_config(config)
    mode = str(provider.get("mode", "file")).lower()
    if mode == "http":
        return get_code_from_http(email_address, config)
    if mode == "file":
        return get_code_from_file(email_address, config)
    if mode in ("outlook_graph", "outlook", "graph"):
        return get_code_from_outlook_graph(email_address, config)
    raise Exception(f"未知 verification_code_bridge.mode: {mode}")


def wait_for_code(email_address, config, timeout_seconds=60):
    provider = _provider_config(config)
    poll_interval = max(0.5, float(provider.get("poll_interval", 2)))
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            code = get_code_once(email_address, config)
            if code:
                return code
        except Exception as e:
            last_error = e
        time.sleep(poll_interval)
    if last_error:
        raise Exception(f"{timeout_seconds} 秒内未从本地验证码桥接层取到验证码，最后错误: {last_error}")
    raise Exception(f"{timeout_seconds} 秒内未从本地验证码桥接层取到验证码")
