"""
Outlook 邮箱接码模块
通过 Microsoft Graph API + refresh_token 自动读取验证码邮件

账号格式: 邮箱----密码----client_id----refresh_token
"""

import json
import re
import time
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

# Microsoft OAuth2 端点
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# 默认 scope
DEFAULT_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"


class OutlookAccount:
    """单个 Outlook 账号"""

    def __init__(self, email: str, password: str, client_id: str, refresh_token: str):
        self.email = email
        self.password = password
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.access_token = ""
        self.token_expires_at = 0

    def is_token_valid(self) -> bool:
        return self.access_token and time.time() < self.token_expires_at - 60

    def refresh_access_token(self) -> str:
        """用 refresh_token 换取新的 access_token"""
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": DEFAULT_SCOPE,
        }
        resp = requests.post(MICROSOFT_TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            error_detail = resp.text[:500]
            raise Exception(
                f"刷新 token 失败 [{resp.status_code}]: {error_detail}"
            )

        result = resp.json()
        self.access_token = result["access_token"]
        self.token_expires_at = time.time() + result.get("expires_in", 3600)

        # 如果返回了新的 refresh_token，更新它
        new_refresh = result.get("refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh

        logger.info(f"[{self.email}] access_token 刷新成功，有效期 {result.get('expires_in', 3600)}s")
        return self.access_token

    def get_access_token(self) -> str:
        """获取有效的 access_token，过期则自动刷新"""
        if not self.is_token_valid():
            self.refresh_access_token()
        return self.access_token

    def get_messages(self, top: int = 10, since_minutes: int = 5, sender_filter: str = "") -> list:
        """
        获取最近的邮件列表

        Args:
            top: 获取数量
            since_minutes: 只获取最近 N 分钟内的邮件
            sender_filter: 可选，按发件人过滤
        """
        token = self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        # 构建过滤条件
        since_time = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        filter_parts = [f"receivedDateTime ge {since_time}"]
        if sender_filter:
            filter_parts.append(f"contains(from/emailAddress/address, '{sender_filter}')")

        params = {
            "$top": top,
            "$orderby": "receivedDateTime desc",
            "$select": "subject,body,from,receivedDateTime",
            "$filter": " and ".join(filter_parts),
        }

        url = f"{GRAPH_API_BASE}/me/messages"
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 401:
            # token 可能过期，强制刷新重试
            self.access_token = ""
            token = self.get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code != 200:
            raise Exception(f"获取邮件失败 [{resp.status_code}]: {resp.text[:300]}")

        data = resp.json()
        return data.get("value", [])

    def get_latest_code(
        self,
        since_minutes: int = 5,
        sender_filter: str = "",
        code_pattern: str = r"(?<!\d)(\d{6})(?!\d)",
    ) -> str:
        """
        获取最新的验证码

        Args:
            since_minutes: 搜索最近 N 分钟的邮件
            sender_filter: 按发件人过滤
            code_pattern: 验证码正则
        """
        messages = self.get_messages(top=5, since_minutes=since_minutes, sender_filter=sender_filter)

        for msg in messages:
            subject = msg.get("subject", "")
            body_content = msg.get("body", {}).get("content", "")

            # 先从纯文本中提取
            text = re.sub(r"<[^>]+>", " ", body_content)  # 去除 HTML 标签
            text = re.sub(r"\s+", " ", text)

            # 尝试从 subject 和 body 中提取验证码
            for source in [subject, text]:
                match = re.search(code_pattern, source)
                if match:
                    code = match.group(1)
                    logger.info(
                        f"[{self.email}] 找到验证码: {code} (来自: {msg.get('from', {}).get('emailAddress', {}).get('address', 'unknown')})"
                    )
                    return code

        return ""


class OutlookAccountPool:
    """Outlook 账号池管理"""

    def __init__(self, accounts_file: str = "outlook_token_accounts.txt"):
        self.accounts_file = Path(accounts_file)
        self.accounts: dict[str, OutlookAccount] = {}
        self._load_accounts()

    def _load_accounts(self):
        """从文件加载账号"""
        if not self.accounts_file.exists():
            logger.warning(f"账号文件不存在: {self.accounts_file}")
            return

        for line in self.accounts_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = re.split(r"\s*----\s*", line)
            if len(parts) < 4:
                logger.warning(f"格式不正确，跳过: {line[:50]}...")
                continue

            email_addr = parts[0].strip()
            password = parts[1].strip()
            client_id = parts[2].strip()
            refresh_token = parts[3].strip()

            account = OutlookAccount(email_addr, password, client_id, refresh_token)
            self.accounts[email_addr.lower()] = account

        logger.info(f"已加载 {len(self.accounts)} 个 Outlook 账号")

    def get_account(self, email_address: str) -> OutlookAccount | None:
        """根据邮箱获取账号"""
        return self.accounts.get(email_address.lower())

    def list_emails(self) -> list[str]:
        """列出所有邮箱"""
        return list(self.accounts.keys())

    def add_account(self, email: str, password: str, client_id: str, refresh_token: str):
        """动态添加账号"""
        account = OutlookAccount(email, password, client_id, refresh_token)
        self.accounts[email.lower()] = account
        return account

    def save_tokens(self, output_file: str = "outlook_tokens_updated.txt"):
        """保存更新后的 refresh_token（因为 Microsoft 可能会轮换 token）"""
        lines = []
        for account in self.accounts.values():
            lines.append(f"{account.email}----{account.password}----{account.client_id}----{account.refresh_token}")

        Path(output_file).write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"已保存 {len(lines)} 个账号的最新 token 到 {output_file}")


def wait_for_outlook_code(
    email_address: str,
    pool: OutlookAccountPool,
    timeout_seconds: int = 120,
    poll_interval: float = 3,
    since_minutes: int = 5,
    sender_filter: str = "",
    code_pattern: str = r"(?<!\d)(\d{6})(?!\d)",
) -> str:
    """
    轮询等待验证码

    Args:
        email_address: 目标邮箱
        pool: 账号池
        timeout_seconds: 超时时间
        poll_interval: 轮询间隔
        since_minutes: 搜索最近 N 分钟的邮件
        sender_filter: 按发件人过滤
        code_pattern: 验证码正则

    Returns:
        验证码字符串，超时则抛出异常
    """
    account = pool.get_account(email_address)
    if not account:
        raise Exception(f"账号池中没有找到: {email_address}")

    deadline = time.time() + timeout_seconds
    last_error = None

    logger.info(f"[{email_address}] 开始等待验证码 (超时 {timeout_seconds}s)...")

    while time.time() < deadline:
        try:
            code = account.get_latest_code(
                since_minutes=since_minutes,
                sender_filter=sender_filter,
                code_pattern=code_pattern,
            )
            if code:
                return code
        except Exception as e:
            last_error = e
            logger.warning(f"[{email_address}] 获取邮件出错: {e}")

        time.sleep(poll_interval)

    if last_error:
        raise Exception(f"[{email_address}] {timeout_seconds}s 内未收到验证码，最后错误: {last_error}")
    raise Exception(f"[{email_address}] {timeout_seconds}s 内未收到验证码")


# ==================== 对接 code_provider.py 的桥接 ====================

_global_pool: OutlookAccountPool | None = None


def get_global_pool(accounts_file: str = "outlook_token_accounts.txt") -> OutlookAccountPool:
    """获取全局账号池单例"""
    global _global_pool
    if _global_pool is None:
        _global_pool = OutlookAccountPool(accounts_file)
    return _global_pool


def get_code_from_outlook(email_address: str, config: dict) -> str:
    """
    供 code_provider.py 调用的接口
    在 config.json 中设置:
        "verification_code_bridge": {"mode": "outlook_graph"}
    """
    outlook_cfg = config.get("outlook_graph", {})
    accounts_file = outlook_cfg.get("accounts_file", "outlook_token_accounts.txt")
    since_minutes = outlook_cfg.get("since_minutes", 5)
    sender_filter = outlook_cfg.get("sender_filter", "")

    pool = get_global_pool(accounts_file)
    account = pool.get_account(email_address)
    if not account:
        return ""

    try:
        code = account.get_latest_code(
            since_minutes=since_minutes,
            sender_filter=sender_filter,
        )
        return code
    except Exception as e:
        logger.warning(f"[{email_address}] Graph API 获取验证码失败: {e}")
        return ""


# ==================== 命令行测试 ====================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    accounts_file = "outlook_token_accounts.txt"
    if not Path(accounts_file).exists():
        print(f"请先创建 {accounts_file}，格式：")
        print("邮箱----密码----client_id----refresh_token")
        sys.exit(1)

    pool = OutlookAccountPool(accounts_file)
    emails = pool.list_emails()
    print(f"已加载 {len(emails)} 个账号:")
    for e in emails:
        print(f"  - {e}")

    if not emails:
        sys.exit(0)

    # 测试第一个账号
    test_email = emails[0]
    print(f"\n测试账号: {test_email}")

    account = pool.get_account(test_email)
    try:
        account.refresh_access_token()
        print("✅ Token 刷新成功")

        messages = account.get_messages(top=3, since_minutes=60)
        print(f"最近 60 分钟内有 {len(messages)} 封邮件:")
        for msg in messages:
            subject = msg.get("subject", "(无主题)")
            from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
            print(f"  - [{from_addr}] {subject}")

        code = account.get_latest_code(since_minutes=60)
        if code:
            print(f"✅ 找到验证码: {code}")
        else:
            print("❌ 最近 60 分钟内没有找到验证码")

    except Exception as e:
        print(f"❌ 错误: {e}")

    # 保存可能更新的 token
    pool.save_tokens()
