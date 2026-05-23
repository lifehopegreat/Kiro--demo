import email
import imaplib
import json
import os
import re
from email.header import decode_header
from pathlib import Path


def load_dotenv(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config():
    config_path = Path("config.json")
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
        return {
            "email": config["gmail"]["email"],
            "app_password": config["gmail"]["app_password"],
        }

    load_dotenv()
    return {
        "email": os.getenv("GMAIL_EMAIL", ""),
        "app_password": os.getenv("GMAIL_APP_PASSWORD", ""),
    }


def decode_subject(value):
    if not value:
        return ""

    parts = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return "".join(parts)


def decode_payload(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(payload)


def get_body(msg):
    body_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() in ("text/plain", "text/html"):
                body_parts.append(decode_payload(part))
    else:
        body_parts.append(decode_payload(msg))

    return "\n".join(body_parts)


def extract_code(text):
    patterns = [
        r"(?:verification\s*code|security\s*code|temporary\s*code|code)[^\d]{0,40}(\d{4,8})",
        r"(?<!\d)(\d{6})(?!\d)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


config = load_config()
email_address = config["email"]
password = config["app_password"]

if not email_address or not password:
    raise SystemExit("Missing Gmail config. Create config.json or fill GMAIL_EMAIL/GMAIL_APP_PASSWORD in .env.")

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(email_address, password)
mail.select("INBOX")

print("=== Searching recent INBOX messages for verification codes ===")

result, data = mail.search(None, "ALL")
if result != "OK" or not data or not data[0]:
    mail.logout()
    raise SystemExit("No emails found in INBOX.")

email_ids = data[0].split()[-80:]
found = False

for eid in reversed(email_ids):
    result, msg_data = mail.fetch(eid, "(BODY.PEEK[])")
    if result != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
        continue

    msg = email.message_from_bytes(msg_data[0][1])
    subject = decode_subject(msg.get("Subject"))
    subject_lower = subject.lower()

    print(f"Subject: {subject}")

    likely_code_email = any(
        keyword in subject_lower
        for keyword in ("chatgpt", "openai", "verification", "verify", "code", "验证码")
    )
    if not likely_code_email:
        continue

    body = get_body(msg)
    code = extract_code(subject + "\n" + body)

    if code:
        Path("测试.txt").write_text(code + "\n", encoding="utf-8")
        print(f"OK: extracted verification code: {code}")
        print("Wrote code to 测试.txt")
        found = True
        break

    print("Matched a likely verification email, but no 4-8 digit code was found.")
    print(body[:500])

mail.logout()

if not found:
    print("No verification code extracted from recent INBOX messages.")
