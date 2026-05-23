import asyncio
import json
import aioimaplib
from email.header import decode_header

with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

GMAIL_EMAIL = CONFIG["gmail"]["email"]
APP_PASSWORD = CONFIG["gmail"]["app_password"]

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

async def main():
    imap = aioimaplib.IMAP4_SSL("imap.gmail.com", 993)
    await imap.wait_hello_from_server()
    await imap.login(GMAIL_EMAIL, APP_PASSWORD)

    folders = ["INBOX", "[Gmail]/Spam", "[Gmail]/Promotions"]

    for folder in folders:
        try:
            await imap.select(folder)
            print(f"\n=== 正在搜索文件夹: {folder} ===")
        except Exception as e:
            print(f"无法打开文件夹 {folder}: {e}")
            continue

        _, messages = await imap.search("ALL")
        email_ids = messages[0].split()[-30:]  # 只看最近30封

        for email_id in email_ids:
            try:
                result, msg_data = await imap.fetch(email_id, "(RFC822.HEADER)")
                if result != "OK":
                    continue

                # 提取 Subject
                for item in msg_data:
                    if isinstance(item, bytes) and b"Subject:" in item:
                        subject_line = item.decode(errors="ignore")
                        subject = subject_line.replace("Subject: ", "").strip()
                        decoded_subject = decode_subject(subject)
                        print(f"主题: {decoded_subject}")
                        break
            except:
                continue

    await imap.logout()
    print("\n搜索完成")

asyncio.run(main())