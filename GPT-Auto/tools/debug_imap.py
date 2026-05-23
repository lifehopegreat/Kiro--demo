import imaplib
import email
from email.header import decode_header
import json

with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

EMAIL = CONFIG["gmail"]["email"]
PASSWORD = CONFIG["gmail"]["app_password"]

def decode_subject(value):
    if not value:
        return ""
    parts = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="ignore"))
        else:
            parts.append(str(part))
    return "".join(parts)

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(EMAIL, PASSWORD)

folders = ["INBOX", "[Gmail]/Spam", "[Gmail]/Promotions"]

for folder in folders:
    try:
        mail.select(folder)
        print(f"\n=== 正在搜索文件夹: {folder} ===")
    except:
        continue

    result, data = mail.search(None, "ALL")
    if result != "OK":
        continue

    email_ids = data[0].split()[-30:]

    for eid in email_ids:
        result, msg_data = mail.fetch(eid, "(RFC822.HEADER)")
        if result != "OK":
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_subject(msg["Subject"])
        print(f"主题: {subject}")

mail.logout()
print("\n搜索完成")