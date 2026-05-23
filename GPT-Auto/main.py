import asyncio
import json
import random
import time
import logging
from datetime import datetime
from pathlib import Path
import imaplib
import email
from email.header import decode_header
from playwright.async_api import async_playwright
import re
import html
import ssl

from roxy_client import launch_roxy_profile
from code_provider import wait_for_code as wait_for_local_code

# ==================== 配置加载 ====================
with open("config.json", "r", encoding="utf-8-sig") as f:
    CONFIG = json.load(f)
CONFIG.setdefault("files", {})
CONFIG["files"].setdefault("accounts", "accounts.txt")
CONFIG["files"].setdefault("cardmi", "cardmi.txt")
CONFIG["files"].setdefault("phone", "phone.txt")
CONFIG["files"].setdefault("outlook_accounts", "outlook_accounts.txt")
CONFIG["files"].setdefault("verification_codes", "codes.json")
CONFIG.setdefault("email_provider", "outlook_manual")
CONFIG.setdefault("verification_code_bridge", {})
CONFIG["verification_code_bridge"].setdefault("mode", "file")
CONFIG["verification_code_bridge"].setdefault("file", CONFIG["files"]["verification_codes"])
CONFIG["verification_code_bridge"].setdefault("url", "http://127.0.0.1:8787/code")
CONFIG["verification_code_bridge"].setdefault("poll_interval", 2)
CONFIG["verification_code_bridge"].setdefault("consume", True)

def is_outlook_provider():
    return str(CONFIG.get("email_provider", "gmail")).lower().startswith("outlook")

def validate_config():
    provider = str(CONFIG.get("email_provider", "gmail")).lower()
    if provider.startswith("outlook"):
        if not Path(CONFIG["files"]["outlook_accounts"]).exists():
            raise Exception(f"{CONFIG['files']['outlook_accounts']} 不存在；请每行写一个 Outlook 邮箱")
        return
    gmail = CONFIG.get("gmail") or {}
    if not str(gmail.get("email", "")).strip():
        raise Exception("config.json 缺少 gmail.email")
    if not str(gmail.get("app_password", "")).strip():
        raise Exception("config.json 缺少 gmail.app_password；当前为空，无法读取验证码邮件")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("automation.log", encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

FIRST_NAMES = [
    "Alex", "Ryan", "Ethan", "Noah", "Liam", "Lucas", "Mason", "Logan",
    "Emma", "Olivia", "Sophia", "Mia", "Ava", "Grace", "Chloe", "Nora",
]
LAST_NAMES = [
    "Chen", "Wang", "Li", "Zhang", "Liu", "Yang", "Zhao", "Huang",
    "Smith", "Johnson", "Brown", "Davis", "Wilson", "Taylor", "Martin",
]
EMAIL_WORDS = [
    "Artnme", "Garden", "Silver", "Forest", "Bridge", "Planet", "Rocket", "Coffee",
    "Orange", "Marble", "Window", "Summer", "Winter", "Spring", "Castle", "Rivera",
]

US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "IA": "Iowa",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "MA": "Massachusetts", "MD": "Maryland",
    "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri",
    "MS": "Mississippi", "MT": "Montana", "NC": "North Carolina", "ND": "North Dakota",
    "NE": "Nebraska", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NV": "Nevada", "NY": "New York", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VA": "Virginia", "VT": "Vermont", "WA": "Washington", "WI": "Wisconsin",
    "WV": "West Virginia", "WY": "Wyoming", "DC": "District of Columbia",
}

def random_full_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def random_paypal_password():
    letters = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    symbols = "!@#$%"
    core = "".join(random.choice(letters + digits) for _ in range(10))
    return f"Pp{core}{random.choice(digits)}{random.choice(symbols)}"

def email_for_sequence(sequence):
    word = EMAIL_WORDS[((sequence - 1) // 999) % len(EMAIL_WORDS)]
    number = ((sequence - 1) % 999) + 1
    return f"{word}{number:03d}@{CONFIG['domain']}"

def collect_used_emails():
    used = set()
    if is_outlook_provider():
        email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", re.IGNORECASE)
    else:
        email_pattern = re.compile(rf"\b[A-Za-z][A-Za-z0-9._%+-]*@{re.escape(CONFIG['domain'])}\b", re.IGNORECASE)
    paths = [
        USED_EMAIL_FILE,
        Path(CONFIG["files"]["accounts"]),
        Path("payment_links.txt"),
        Path("automation.log"),
        STATE_FILE,
    ]
    for path in paths:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        used.update(email.lower() for email in email_pattern.findall(text))
    return used

def mark_email_used(email_address):
    used = collect_used_emails()
    if email_address.lower() in used:
        return
    with open(USED_EMAIL_FILE, "a", encoding="utf-8") as f:
        f.write(email_address + "\n")

def load_outlook_accounts():
    path = Path(CONFIG["files"]["outlook_accounts"])
    accounts = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        email_address = re.split(r"\s*(?:----|\||,|\s)\s*", line, maxsplit=1)[0].strip()
        if "@" in email_address:
            accounts.append(email_address)
    return accounts

def select_outlook_email():
    state = load_state()
    used = collect_used_emails()
    current = str(state.get("email") or "").strip()
    accounts = load_outlook_accounts()
    if current and current.lower() not in used and current in accounts:
        mark_email_used(current)
        return current
    for email_address in accounts:
        if email_address.lower() not in used:
            mark_email_used(email_address)
            update_state(last_generated_email=email_address)
            return email_address
    raise Exception(f"{CONFIG['files']['outlook_accounts']} 里没有可用 Outlook 邮箱；如需继续请追加新邮箱")

def generate_email_address():
    if is_outlook_provider():
        return select_outlook_email()
    state = load_state()
    sequence = int(state.get("email_sequence", 1))
    used = collect_used_emails()
    while True:
        email_address = email_for_sequence(sequence)
        if email_address.lower() not in used:
            break
        sequence += 1
    mark_email_used(email_address)
    update_state(email_sequence=sequence + 1, last_generated_email=email_address)
    return email_address

STATE_FILE = Path("run_state.json")
USED_EMAIL_FILE = Path("used_emails.txt")
USED_CARDMI_FILE = Path("used_cardmi.txt")
CARD_RESULT_FILE = Path("cards_result.jsonl")
MANUAL_CARD_INFO_FILE = Path("manual_card_info.json")
MIN_CARD_EXPIRY_YEAR_2D = 28
SCREENSHOT_DIR = Path("screenshots")

class RestartAccountRequired(Exception):
    pass

class PaidCheckoutRequired(RestartAccountRequired):
    pass

class PayPalBlocked(Exception):
    pass

def now_text():
    return datetime.now().isoformat(timespec="seconds")

def screenshot_file(category, prefix):
    folder = SCREENSHOT_DIR / category
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"{prefix}_{int(time.time() * 1000)}.png")

def mask_value(value, keep=4):
    if not value:
        return ""
    return f"{value[:keep]}...{value[-keep:]}" if len(value) > keep * 2 else "***"

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def update_state(**updates):
    state = load_state()
    state.update(updates)
    state["updated_at"] = now_text()
    save_state(state)
    return state

def clear_state_keys(*keys):
    state = load_state()
    for key in keys:
        state.pop(key, None)
    state["updated_at"] = now_text()
    save_state(state)
    return state

def append_jsonl(path, data):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")

def append_account_status(email_address, account_type):
    account_type = str(account_type).strip()
    with open(CONFIG["files"]["accounts"], "a", encoding="utf-8") as f:
        f.write(f"{email_address}|success|{datetime.now()}-----{account_type}\n")
    logger.info(f"已保存账号状态: {email_address} -----{account_type}")

def load_manual_card_info():
    if not MANUAL_CARD_INFO_FILE.exists():
        return None
    data = json.loads(MANUAL_CARD_INFO_FILE.read_text(encoding="utf-8"))
    if not data.get("enabled", False):
        return None
    return {
        "card_number": str(data.get("card_number", "")).strip(),
        "expiry": str(data.get("expiry", "")).strip(),
        "cvv": str(data.get("cvv", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "name": str(data.get("name", "")).strip(),
        "address": str(data.get("address", "")).strip(),
        "sms_api": str(data.get("sms_api", "")).strip(),
        "raw_text": json.dumps(data, ensure_ascii=False),
    }

def read_used_cardmis():
    if not USED_CARDMI_FILE.exists():
        return set()
    return {
        line.strip()
        for line in USED_CARDMI_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

def select_cardmi():
    cardmi_path = Path(CONFIG["files"]["cardmi"])
    used = read_used_cardmis()
    state = load_state()
    current = state.get("cardmi")
    if current and current not in used:
        return current

    for line in cardmi_path.read_text(encoding="utf-8").splitlines():
        cardmi = line.strip()
        if cardmi and cardmi not in used:
            update_state(cardmi=cardmi, cardmi_masked=mask_value(cardmi), step="cardmi_selected")
            return cardmi

    raise Exception("cardmi.txt 里没有可用卡密；如果文件里有卡密，说明它们都已记录在 used_cardmi.txt")

def mark_cardmi_used(cardmi):
    used = read_used_cardmis()
    if cardmi not in used:
        with open(USED_CARDMI_FILE, "a", encoding="utf-8") as f:
            f.write(cardmi + "\n")

def get_pending_card():
    pending = load_state().get("pending_card")
    if not isinstance(pending, dict):
        return None
    card_info = pending.get("card_info")
    if not isinstance(card_info, dict) or not card_info.get("card_number"):
        return None
    return pending

def save_pending_card(cardmi, card_info, email_address):
    pending = {
        "cardmi": cardmi or "",
        "cardmi_masked": mask_value(cardmi) if cardmi else "",
        "card_info": card_info,
        "saved_at": now_text(),
        "source_email": email_address,
    }
    update_state(
        step="pending_card_saved",
        pending_card=pending,
        cardmi=cardmi or "",
        cardmi_masked=mask_value(cardmi) if cardmi else "",
        card_number_masked=mask_value(card_info.get("card_number", "")),
    )
    return pending

def finish_pending_card():
    pending = get_pending_card()
    if pending:
        cardmi = pending.get("cardmi", "")
        if cardmi:
            mark_cardmi_used(cardmi)
        clear_state_keys("pending_card", "cardmi", "cardmi_masked")

def load_phone_info():
    phone_path = Path(CONFIG["files"]["phone"])
    if not phone_path.exists():
        raise Exception(f"{phone_path} 不存在")

    for line in phone_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        raw_phone = parts[0].strip()
        sms_url = parts[1].strip() if len(parts) > 1 else ""
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) == 11 and digits.startswith("1"):
            paypal_phone = digits[1:]
        elif raw_phone.strip().startswith("+1") and len(digits) > 10:
            paypal_phone = digits[-10:]
        else:
            paypal_phone = digits
        if paypal_phone:
            return {
                "raw_phone": raw_phone,
                "paypal_phone": paypal_phone,
                "sms_url": sms_url,
            }

    raise Exception("phone.txt 里没有可用手机号")

def extract_virtual_card_info(text):
    compact = re.sub(r"\s+", " ", text)
    card_number = re.search(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", compact)
    cvv = re.search(r"(?:CVV|CVC|安全码|验证码)[^\d]{0,20}(\d{3,4})", compact, re.IGNORECASE)

    def labeled_value(labels):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        label_pattern = "|".join(re.escape(label) for label in labels)
        for index, line in enumerate(lines):
            if not any(label.lower() in line.lower() for label in labels):
                continue
            value = re.sub(label_pattern, "", line, flags=re.IGNORECASE)
            value = value.replace("点击复制", "").strip(" ：:-")
            if value:
                return re.sub(r"\s+", " ", value).strip()
            for next_line in lines[index + 1:index + 4]:
                value = next_line.replace("点击复制", "").strip(" ：:-")
                if value and not any(label.lower() == value.lower() for label in labels):
                    return re.sub(r"\s+", " ", value).strip()
        return ""

    name = labeled_value(["姓名", "NAME"])
    address = labeled_value(["地址", "ADDRESS"])
    phone_value = labeled_value(["电话", "PHONE"])
    expiry_value = labeled_value(["有效期", "EXPIRY"])
    expiry = re.search(r"(?<!\d)(20\d{2})\s*/\s*(0?[1-9]|1[0-2])(?!\d)", expiry_value) or re.search(
        r"(?<!\d)(0?[1-9]|1[0-2])\s*/\s*(\d{2,4})(?!\d)",
        expiry_value,
    )
    if not expiry:
        expiry = (
            re.search(r"(?<!\d)(20\d{2})\s*/\s*(0?[1-9]|1[0-2])(?!\d)", compact)
            or re.search(r"(?<!\d)(0?[1-9]|1[0-2])\s*/\s*(\d{2,4})(?!\d)", compact)
        )
    phone = re.search(r"\+?\d[\d\s-]{7,}", phone_value)
    return {
        "card_number": re.sub(r"\D", "", card_number.group(0)) if card_number else "",
        "expiry": expiry.group(0).replace(" ", "") if expiry else "",
        "cvv": cvv.group(1) if cvv else "",
        "phone": re.sub(r"\s+", "", phone.group(0)) if phone else "",
        "name": name.upper(),
        "address": address,
        "raw_text": text[:4000],
    }

def split_us_address(address):
    cleaned = re.sub(r"\s+", " ", address.replace("，", ",")).strip()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    street = parts[0] if parts else cleaned
    tail = parts[1] if len(parts) > 1 else ""
    city = ""
    postal_code = ""
    state = ""
    match = re.search(r"(.+?)\s+(\d{5})(?:-\d{4})?(?:\s+([A-Z]{2}))?$", tail, re.IGNORECASE)
    if match:
        city = match.group(1).strip()
        postal_code = match.group(2)
        state = (match.group(3) or "").upper()
    if not state and postal_code:
        state = state_from_zip(postal_code)
    return {
        "street": street,
        "city": city,
        "postal_code": postal_code,
        "state": state,
        "state_name": US_STATE_NAMES.get(state, state),
        "country": "United States",
    }

def state_from_zip(postal_code):
    try:
        prefix = int(str(postal_code)[:3])
    except (TypeError, ValueError):
        return ""

    if prefix == 200 or 202 <= prefix <= 205:
        return "DC"
    if prefix == 201 or 220 <= prefix <= 246:
        return "VA"
    if 206 <= prefix <= 219:
        return "MD"

    zip_ranges = [
        ("MA", 10, 27), ("RI", 28, 29), ("NH", 30, 38), ("ME", 39, 49),
        ("VT", 50, 59), ("CT", 60, 69), ("NJ", 70, 89), ("NY", 100, 149),
        ("PA", 150, 196), ("DE", 197, 199), ("WV", 247, 268), ("NC", 270, 289), ("SC", 290, 299),
        ("GA", 300, 319), ("FL", 320, 349), ("AL", 350, 369), ("TN", 370, 385),
        ("MS", 386, 397), ("KY", 400, 427), ("OH", 430, 459), ("IN", 460, 479),
        ("MI", 480, 499), ("IA", 500, 528), ("WI", 530, 549), ("MN", 550, 567),
        ("SD", 570, 577), ("ND", 580, 588), ("MT", 590, 599), ("IL", 600, 629),
        ("MO", 630, 658), ("KS", 660, 679), ("NE", 680, 693), ("LA", 700, 714),
        ("AR", 716, 729), ("OK", 730, 749), ("TX", 750, 799), ("CO", 800, 816),
        ("WY", 820, 831), ("ID", 832, 838), ("UT", 840, 847), ("AZ", 850, 865),
        ("NM", 870, 884), ("NV", 889, 898), ("CA", 900, 961), ("OR", 970, 979),
        ("WA", 980, 994),
    ]
    if prefix == 5 or prefix == 63:
        return "NY"
    if prefix == 733 or prefix == 885:
        return "TX"
    for state, start, end in zip_ranges:
        if start <= prefix <= end:
            return state
    return ""

def format_paypal_expiry(expiry):
    cleaned = str(expiry or "").strip()

    def validate_year(year):
        year_2d = int(str(year)[-2:])
        if year_2d < MIN_CARD_EXPIRY_YEAR_2D:
            raise Exception(f"卡片有效期年份疑似解析错误，低于 20{MIN_CARD_EXPIRY_YEAR_2D}: {expiry}")
        return year_2d

    match = re.search(r"(?<!\d)(20\d{2})\s*/\s*(0?[1-9]|1[0-2])(?!\d)", cleaned)
    if match:
        year = validate_year(match.group(1))
        month = int(match.group(2))
        return f"{month:02d}/{year:02d}"

    match = re.search(r"(?<!\d)(0?[1-9]|1[0-2])\s*/\s*(\d{2,4})(?!\d)", cleaned)
    if match:
        month = int(match.group(1))
        year = validate_year(match.group(2))
        return f"{month:02d}/{year:02d}"

    match = re.search(r"(?<!\d)(\d{2})\s*/\s*(0?[1-9]|1[0-2])(?!\d)", cleaned)
    if match:
        year = validate_year(match.group(1))
        month = int(match.group(2))
        return f"{month:02d}/{year:02d}"

    raise Exception(f"无法识别卡片有效期: {expiry}")

def extract_total_due_today_value(page_text):
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    total_label = re.compile(r"Total\s+due\s+today", re.IGNORECASE)
    money_pattern = re.compile(r"(?:US\$|USD|EUR|GBP|[$€£])?\s*\d+(?:[.,]\d{2})?")

    for index, line in enumerate(lines):
        if not total_label.search(line):
            continue

        same_line_tail = total_label.sub("", line).strip()
        candidates = []
        if same_line_tail:
            candidates.append(same_line_tail)
        candidates.extend(lines[index + 1:index + 5])

        for candidate in candidates:
            match = money_pattern.search(candidate)
            if match:
                return match.group(0).strip()

    return ""

def is_zero_money(value):
    numeric = re.sub(r"[^0-9,.-]", "", str(value or ""))
    if not numeric:
        return False

    if "," in numeric and "." in numeric:
        numeric = numeric.replace(",", "")
    elif "," in numeric:
        numeric = numeric.replace(",", ".")

    try:
        return abs(float(numeric)) < 0.005
    except ValueError:
        return False

async def assert_total_due_today_zero(payment_page, email_address, timeout=30000):
    deadline = time.time() + timeout / 1000
    total_due = ""
    page_text = ""

    while time.time() < deadline:
        try:
            page_text = await payment_page.locator("body").inner_text(timeout=5000)
            total_due = extract_total_due_today_value(page_text)
            if total_due:
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not total_due:
        logger.warning("没有检测到 Total due today，继续流程")
        update_state(step="checkout_total_due_not_found", email=email_address, checkout_url=payment_page.url)
        return

    logger.info(f"检测到 Total due today: {total_due}")
    update_state(
        step="checkout_total_due_checked",
        email=email_address,
        checkout_url=payment_page.url,
        total_due_today=total_due,
    )

    if is_zero_money(total_due):
        logger.info("✅ Total due today 为 0.00，继续流程")
        return

    screenshot_path = screenshot_file("paid_checkout", "paid_checkout")
    try:
        await payment_page.screenshot(path=screenshot_path, full_page=True)
    except Exception:
        screenshot_path = ""

    update_state(
        step="paid_checkout_restart",
        email=email_address,
        checkout_url=payment_page.url,
        total_due_today=total_due,
        paid_checkout_screenshot=screenshot_path,
    )
    raise PaidCheckoutRequired(f"Total due today 不是 0.00（当前 {total_due}），中断当前账号并重新开始注册")

# ==================== 人类行为模拟 ====================
async def human_delay(min_s=None, max_s=None):
    min_s = min_s or CONFIG["delays"]["min_action"]
    max_s = max_s or CONFIG["delays"]["max_action"]
    await asyncio.sleep(random.uniform(min_s, max_s))

async def human_type(page, selector, text):
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=30000)
    try:
        await locator.click(timeout=5000)
        await page.keyboard.press("Control+A")
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.04, 0.15))
    except Exception as e:
        logger.warning(f"普通输入失败，改用 fill: {selector} | {e}")
        await locator.fill(text, timeout=10000)
    await human_delay(0.4, 0.9)

async def fill_profile_name_and_age(page, full_name):
    async def fill_label(label, value, timeout=2500):
        try:
            locator = page.get_by_label(label).first
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.fill(value, timeout=10000)
            logger.info(f"已填写 {label}: {value}")
            return True
        except Exception:
            return False

    name_filled = await fill_label("Full name", full_name)
    if not name_filled:
        name_filled = await fill_first_visible(
            page,
            [
                "input[placeholder='Full name']",
                "input[name='name']",
                "input[autocomplete='name']",
                "input[aria-label*='Full name' i]",
            ],
            full_name,
            "Full name",
        )
    await human_delay(1, 2)

    if await fill_label("Birthday", "01/01/1996"):
        await human_delay(1, 2)
        return

    age_input = page.locator(
        "input[placeholder='Age'], input[aria-label='Age'], input[name='age']"
    ).first
    try:
        await age_input.wait_for(state="visible", timeout=3000)
        await age_input.fill("25", timeout=10000)
        logger.info("已填写 Age: 25")
        await human_delay(1, 2)
        return
    except Exception:
        pass

    filled_by_order = await page.evaluate(
        """({ fullName, birthday }) => {
            const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0
                    && !el.disabled
                    && !el.readOnly
                    && !['hidden', 'file', 'submit', 'button', 'checkbox', 'radio'].includes((el.type || '').toLowerCase());
            };
            const setValue = (el, value) => {
                el.focus();
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, value);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            };
            const textFor = (el) => [
                el.placeholder,
                el.name,
                el.id,
                el.getAttribute('aria-label'),
                el.closest('label')?.innerText,
                el.parentElement?.innerText,
            ].join(' ').toLowerCase();
            const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
            const nameInput = inputs.find((el) => /full\\s*name|name/.test(textFor(el)));
            const birthdayInput = inputs.find((el) => /birthday|birth|date of birth/.test(textFor(el)));
            if (nameInput && birthdayInput) {
                setValue(nameInput, fullName);
                setValue(birthdayInput, birthday);
                return true;
            }
            const editable = inputs.filter((el) => {
                const text = textFor(el);
                return !text.includes('email') && !text.includes('code') && !text.includes('upload');
            });
            if (editable.length < 2) {
                return false;
            }
            setValue(editable[0], fullName);
            setValue(editable[1], birthday);
            return true;
        }""",
        {"fullName": full_name, "birthday": "01/01/1996"},
    )
    if not filled_by_order:
        raise Exception("没有找到可填写的 Full name / Birthday 或 Age")
    logger.info("已按输入框顺序填写 Full name 和 Birthday")
    await human_delay(1, 2)

async def human_click(page, selector):
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=30000)
    try:
        await locator.hover(timeout=5000)
    except Exception as e:
        logger.warning(f"hover 失败，直接点击: {selector} | {e}")
    await human_delay(0.3, 0.7)
    try:
        await locator.click(timeout=10000)
    except Exception as e:
        logger.warning(f"普通点击失败，改用 force click: {selector} | {e}")
        await locator.click(force=True, timeout=10000)
    await human_delay()

async def fill_first_visible(page, selectors, value, label):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=3000)
            await locator.fill(value, timeout=10000)
            logger.info(f"已填写 {label}")
            return True
        except Exception:
            continue
    logger.warning(f"没有找到可填写的 {label}")
    return False

async def click_if_visible(page, selectors, label, timeout=3000):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.click(timeout=10000)
            logger.info(f"已点击 {label}")
            return True
        except Exception:
            continue
    return False

async def open_outlook_for_manual_code(context, email_address):
    outlook_page = await context.new_page()
    await outlook_page.goto("https://outlook.live.com/", wait_until="domcontentloaded", timeout=60000)
    await human_delay(2, 4)
    await click_if_visible(
        outlook_page,
        [
            "a:has-text('Sign in')",
            "a:has-text('Sign In')",
            "text=Sign in",
            "text=Sign In",
        ],
        "Outlook Sign in",
        timeout=5000,
    )
    await human_delay(1, 2)
    for selector in ["input[type='email']", "input[name='loginfmt']", "input[placeholder*='Email' i]"]:
        try:
            field = outlook_page.locator(selector).first
            await field.wait_for(state="visible", timeout=5000)
            await field.fill(email_address, timeout=10000)
            logger.info("已打开 Outlook 登录页并填入邮箱；请手动完成密码/登录，稍后查看验证码")
            return outlook_page
        except Exception:
            continue
    logger.info("已打开 Outlook 页面；请手动登录并查看验证码")
    return outlook_page

async def human_click_first_visible(page, selectors, label):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=5000)
            await locator.scroll_into_view_if_needed(timeout=5000)
            await human_delay(0.3, 0.7)
            try:
                await locator.click(timeout=10000)
            except Exception as e:
                logger.warning(f"{label} 普通点击失败，改用 force click: {selector} | {e}")
                await locator.click(force=True, timeout=10000)
            logger.info(f"已点击 {label}: {selector}")
            await human_delay()
            return
        except Exception:
            continue
    raise Exception(f"没有找到可点击的 {label}")

async def select_paypal_method(page):
    paypal_text = page.locator("text=PayPal").first
    await paypal_text.wait_for(state="visible", timeout=15000)
    box = await paypal_text.bounding_box()
    if box:
        await page.mouse.click(box["x"] - 70, box["y"] + box["height"] / 2)
        await human_delay(1, 2)
        logger.info("已点击 PayPal 前方小圆圈")
        return
    await human_click_first_visible(
        page,
        [
            "input[type='radio'][value*='paypal' i]",
            "label:has-text('PayPal')",
            "text=PayPal",
        ],
        "PayPal 支付方式"
    )

async def check_subscription_terms(page):
    checkboxes = page.locator("input[type='checkbox']:visible")
    count = await checkboxes.count()
    if count > 0:
        checkbox = checkboxes.first
        try:
            checked = await checkbox.is_checked(timeout=1000)
        except Exception:
            checked = False
        if not checked:
            box = await checkbox.bounding_box()
            if box:
                await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await checkbox.click(force=True, timeout=10000)
        await human_delay(0.5, 1)
        logger.info("已勾选订阅协议小方框")
        return

    terms_text = page.locator("text=You'll be charged").first
    await terms_text.wait_for(state="visible", timeout=8000)
    box = await terms_text.bounding_box()
    if box:
        await page.mouse.click(box["x"] - 35, box["y"] + 8)
        await human_delay(0.5, 1)
        logger.info("已点击订阅协议文字左侧小方框")
        return
    raise Exception("没有找到订阅协议小方框")

async def choose_address_autocomplete(page, address_box, address):
    parts = split_us_address(address)
    street_head = parts["street"].split()[0] if parts["street"].split() else address.split()[0]
    inputs = []
    if len(address) > 4:
        inputs = [address[:-1], address, address[:-1], address, address[:-1]]
    else:
        inputs = [address]

    suggestion_selectors = [
        f"[role='option']:has-text('{street_head}')",
        f"li:has-text('{street_head}')",
        f"div:has-text('{street_head}'):visible",
    ]

    for attempt, text in enumerate(inputs, start=1):
        await address_box.fill(text, timeout=10000)
        await human_delay(1.2, 2.0)
        input_box = await address_box.bounding_box()
        for selector in suggestion_selectors:
            try:
                locator = page.locator(selector)
                await locator.first.wait_for(state="visible", timeout=2500)
                count = min(await locator.count(), 8)
                for index in range(count):
                    suggestion = locator.nth(index)
                    if not await suggestion.is_visible(timeout=500):
                        continue
                    box = await suggestion.bounding_box()
                    if not box:
                        continue
                    if input_box and not (
                        box["y"] >= input_box["y"] + input_box["height"] - 5
                        and box["y"] <= input_box["y"] + input_box["height"] + 220
                        and box["x"] <= input_box["x"] + input_box["width"]
                        and box["x"] + box["width"] >= input_box["x"]
                    ):
                        continue
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    await human_delay(1, 2)
                    logger.info(f"已选择地址自动补全项，第 {attempt} 次尝试")
                    return True
            except Exception:
                continue
        logger.info(f"第 {attempt} 次未检测到地址自动补全项，继续切换地址末尾字符")

    return False

async def fetch_chatgpt_access_token(page, max_attempts=8):
    last_status = "no response"
    last_session = {}

    for attempt in range(1, max_attempts + 1):
        logger.info(f"获取 token，第 {attempt} 次尝试...")
        try:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
            await human_delay(4, 7)

            clicked_finish = await click_if_visible(
                page,
                [
                    "button:has-text('Finish creating account')",
                    "button:has-text('Continue')",
                ],
                "注册收尾按钮",
                timeout=3000,
            )
            if clicked_finish:
                await human_delay(5, 8)

            response = await page.goto(
                "https://chatgpt.com/api/auth/session",
                wait_until="networkidle",
                timeout=60000
            )
            last_status = response.status if response else "no response"
            content = await response.text() if response else ""
            if not content.strip() or content.lstrip().startswith("<"):
                content = await page.locator("body").inner_text(timeout=10000)

            try:
                session = json.loads(content)
            except json.JSONDecodeError:
                Path("session_debug.txt").write_text(content[:2000], encoding="utf-8")
                logger.warning(f"Session response 不是 JSON，status={last_status}，继续重试")
                await human_delay(5, 8)
                continue

            last_session = session
            token = session.get("accessToken", "")
            if token:
                return token

            keys = ", ".join(session.keys()) if isinstance(session, dict) else type(session).__name__
            logger.warning(f"Session JSON 暂无 accessToken，status={last_status}，keys={keys}")
            await human_delay(5, 8)
        except Exception as e:
            logger.warning(f"获取 token 第 {attempt} 次失败，继续重试: {e}")
            await human_delay(5, 8)

    Path("session_debug.txt").write_text(json.dumps(last_session, ensure_ascii=False, indent=2), encoding="utf-8")
    raise Exception(f"Session JSON did not contain accessToken after {max_attempts} attempts, status={last_status}; saved to session_debug.txt")

async def generate_payment_link(page, token, email_address):
    logger.info("打开支付链接生成页面...")
    await page.goto("https://payurl.779.chat/", wait_until="networkidle", timeout=60000)
    await human_delay(2, 4)
    await click_if_visible(
        page,
        [
            "button:has-text('我知道了，开始生成')",
            "text=我知道了，开始生成",
        ],
        "支付链接生成指南弹窗",
        timeout=5000,
    )
    await human_delay(1, 2)

    token_box = page.locator("textarea").first
    await token_box.wait_for(state="visible", timeout=30000)
    await token_box.fill(token, timeout=30000)
    await human_delay(1, 2)

    generate_button = page.locator("button:has-text('生成支付链接')").first
    open_button = page.locator("button:visible:has-text('打开链接')").first
    page_text = ""
    links = []

    for attempt in range(1, 31):
        logger.info(f"生成支付链接，第 {attempt} 次尝试...")
        await generate_button.scroll_into_view_if_needed(timeout=30000)
        try:
            await generate_button.click(timeout=10000)
        except Exception as e:
            logger.warning(f"普通点击生成失败，改用 force click: {e}")
            await generate_button.click(force=True, timeout=10000)

        for second in range(10):
            await asyncio.sleep(1)
            page_text = await page.locator("body").inner_text(timeout=10000)
            links = re.findall(r"https?://[^\s\"'<>]+", page_text)
            pay_links = [link for link in links if "pay.openai.com" in link]
            visible_open_buttons = await page.locator("button:visible:has-text('打开链接')").count()
            if pay_links or visible_open_buttons > 0:
                logger.info(f"✅ 支付链接已生成，等待 {second + 1} 秒")
                break
        else:
            pay_links = []
            visible_open_buttons = 0

        if pay_links or visible_open_buttons > 0:
            break

        error_lines = [
            line.strip()
            for line in page_text.splitlines()
            if "请求失败" in line or "failed" in line.lower() or "error" in line.lower()
        ]
        if error_lines:
            logger.warning(f"支付链接生成失败，继续重试: {' | '.join(error_lines[:2])}")
        else:
            logger.warning("10 秒内未生成支付链接，继续重试")
    else:
        Path("payment_debug.txt").write_text(page_text[:4000], encoding="utf-8")
        raise Exception("生成支付链接连续失败 30 次，已保存 payment_debug.txt")

    with open("payment_links.txt", "a", encoding="utf-8") as f:
        f.write(f"{email_address}|{datetime.now()}|{page.url}|{' '.join(links)}\n")

    logger.info("向下查找并打开支付链接...")

    pay_links = [link.rstrip("，,。)") for link in links if "pay.openai.com" in link]
    if pay_links:
        pay_page = await page.context.new_page()
        await pay_page.goto(pay_links[0], wait_until="domcontentloaded", timeout=60000)
        logger.info(f"✅ 已直接打开支付链接: {pay_page.url}")
        return pay_page

    open_button = page.locator("button:visible:has-text('打开链接')").first
    await open_button.scroll_into_view_if_needed(timeout=30000)
    await human_delay(0.5, 1.5)

    pages_before = set(page.context.pages)
    try:
        await open_button.click(timeout=10000)
    except Exception as e:
        logger.warning(f"普通打开失败，改用 force click: {e}")
        await open_button.click(force=True, timeout=10000)

    await human_delay(3, 5)
    new_pages = [candidate for candidate in page.context.pages if candidate not in pages_before]
    if new_pages:
        new_page = new_pages[-1]
        await new_page.wait_for_load_state("domcontentloaded", timeout=60000)
        logger.info(f"✅ 已打开支付链接: {new_page.url}")
        return new_page

    logger.info(f"✅ 已点击打开链接，当前页面: {page.url}")
    return page

async def prepare_paypal_account_payment_page(payment_page, email_address, card_info):
    logger.info("开始处理 PayPal 创建账号前置页面...")
    await payment_page.bring_to_front()
    await payment_page.wait_for_load_state("domcontentloaded", timeout=60000)
    await human_delay(3, 5)
    await handle_paypal_risk_page(payment_page)

    await click_create_account_until_email_page(payment_page)

    await fill_first_visible(
        payment_page,
        [
            "input[type='email']",
            "input[name='email']",
            "input[placeholder*='email' i]",
            "input[aria-label*='email' i]",
        ],
        email_address,
        "PayPal 注册邮箱",
    )
    await human_delay(1, 2)

    await click_continue_to_payment_until_info_page(payment_page)

    email_inputs = payment_page.locator("input[type='email'], input[name='email'], input[placeholder*='Email' i]")
    for index in range(await email_inputs.count()):
        field = email_inputs.nth(index)
        try:
            if not await field.is_visible(timeout=1000):
                continue
            current_value = await field.input_value(timeout=3000)
            if not current_value.strip():
                await field.fill(email_address, timeout=10000)
                logger.info("支付页 Email 为空，已补充注册邮箱")
            else:
                logger.info("支付页 Email 已存在，继续")
            break
        except Exception:
            continue

    phone_info = load_phone_info()
    await fill_paypal_phone_number(payment_page, phone_info["paypal_phone"])
    update_state(
        phone_raw=phone_info["raw_phone"],
        phone_paypal=mask_value(phone_info["paypal_phone"], keep=2),
        phone_sms_url=phone_info["sms_url"],
    )

    await fill_paypal_card_and_billing_info(payment_page, card_info)
    await create_paypal_account_and_open_sms(payment_page, phone_info)
    success_page = await wait_for_plus_subscription_success(payment_page.context, email_address)

    update_state(
        step="paypal_plus_success",
        email=email_address,
        paypal_url=payment_page.url,
        success_url=success_page.url,
        phone_raw=phone_info["raw_phone"],
    )
    logger.info("✅ PayPal 验证完成并检测到 ChatGPT Plus 订阅成功")
    return success_page

async def paypal_body_text(page):
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""

async def handle_paypal_risk_page(page, max_manual_attempts=3):
    for attempt in range(1, max_manual_attempts + 1):
        text = (await paypal_body_text(page)).lower()
        url = page.url

        blocked = (
            "you have been blocked" in text
            or "we couldn't load the security challenge" in text
            or "we couldn’t load the security challenge" in text
        )
        if blocked:
            screenshot_path = screenshot_file("paypal_blocked", "paypal_blocked")
            await page.screenshot(path=screenshot_path, full_page=True)
            update_state(step="paypal_blocked", paypal_url=url, paypal_blocked_screenshot=screenshot_path)
            raise PayPalBlocked(f"PayPal 已拦截：{screenshot_path}")

        challenge = (
            "security challenge" in text
            or "i'm not a robot" in text
            or "i’m not a robot" in text
            or "recaptcha" in text
        )
        if not challenge:
            return False

        screenshot_path = screenshot_file("paypal_security_challenge", "paypal_security_challenge")
        await page.screenshot(path=screenshot_path, full_page=True)
        update_state(
            step="paypal_security_challenge_waiting",
            paypal_url=url,
            paypal_security_challenge_screenshot=screenshot_path,
        )
        logger.warning(f"检测到 PayPal Security Challenge，已截图 {screenshot_path}")
        await page.bring_to_front()
        await asyncio.to_thread(
            input,
            "检测到 PayPal Security Challenge，请在浏览器里手动完成验证，完成后按 Enter 继续..."
        )
        await human_delay(2, 4)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    raise Exception(f"PayPal Security Challenge 手动处理后仍未通过，已尝试 {max_manual_attempts} 次")

async def is_paypal_create_account_email_page(page):
    await handle_paypal_risk_page(page)
    selectors = [
        "button:has-text('Continue to Payment')",
        "input[value='Continue to Payment']",
        "input[placeholder*='Enter email' i]",
        "text=Create a PayPal account",
    ]
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=700)
            return True
        except Exception:
            continue
    return False

async def click_create_account_until_email_page(page, max_attempts=6):
    await handle_paypal_risk_page(page)
    if await is_paypal_create_account_email_page(page):
        logger.info("已在 PayPal 创建账号邮箱页")
        return

    selectors = [
        "button:has-text('Create an Account')",
        "a:has-text('Create an Account')",
        "text=Create an Account",
    ]
    for attempt in range(1, max_attempts + 1):
        logger.info(f"点击 Create an Account，第 {attempt} 次尝试...")
        await human_click_first_visible(page, selectors, "Create an Account")
        for _ in range(20):
            await human_delay(0.8, 1.2)
            await handle_paypal_risk_page(page)
            if await is_paypal_create_account_email_page(page):
                logger.info("✅ 已进入 PayPal 创建账号邮箱页")
                return
        logger.warning("点击 Create an Account 后 20 秒内未进入邮箱页，准备重试")

    raise Exception(f"点击 Create an Account 后未进入邮箱页，已重试 {max_attempts} 次")

async def is_paypal_payment_info_page(page):
    await handle_paypal_risk_page(page)
    selectors = [
        "text=Pay with debit or credit card",
        "input[placeholder*='Phone number' i]",
        "input[name*='phone' i]",
        "input[placeholder*='Card number' i]",
        "input[name*='card' i]",
    ]
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=700)
            return True
        except Exception:
            continue
    return False

async def click_continue_to_payment_until_info_page(page, max_attempts=6):
    selectors = [
        "button:has-text('Continue to Payment')",
        "input[value='Continue to Payment']",
        "text=Continue to Payment",
    ]
    for attempt in range(1, max_attempts + 1):
        await handle_paypal_risk_page(page)
        logger.info(f"点击 Continue to Payment，第 {attempt} 次尝试...")
        await human_click_first_visible(page, selectors, "Continue to Payment")
        for _ in range(25):
            await human_delay(0.8, 1.2)
            await handle_paypal_risk_page(page)
            if await is_paypal_payment_info_page(page):
                logger.info("✅ 已进入 PayPal 支付信息页")
                return
        logger.warning("点击 Continue to Payment 后 25 秒内未进入支付信息页，准备重试")

    raise Exception(f"点击 Continue to Payment 后未进入支付信息页，已重试 {max_attempts} 次")

async def fill_paypal_phone_number(page, paypal_phone):
    selectors = [
        "input[placeholder*='Phone number' i]",
        "input[name*='phone' i]",
        "input[autocomplete='tel-national']",
        "input[type='tel']",
    ]
    filled = await fill_first_visible(page, selectors, paypal_phone, "PayPal 手机号")
    if not filled:
        logger.warning("没有找到可填写的 PayPal 手机号输入框")

async def fill_paypal_card_and_billing_info(page, card_info):
    card_number = re.sub(r"\D", "", card_info.get("card_number", ""))
    expiry = format_paypal_expiry(card_info.get("expiry", ""))
    cvv = str(card_info.get("cvv", "")).strip()
    name = (card_info.get("name") or random_full_name()).strip()
    name_parts = name.split()
    first_name = name_parts[0]
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else first_name

    await fill_first_visible(
        page,
        [
            "input[placeholder*='Card number' i]",
            "input[name*='cardNumber' i]",
            "input[autocomplete='cc-number']",
            "input[inputmode='numeric']",
        ],
        card_number,
        "PayPal 卡号",
    )
    await human_delay(0.5, 1)

    await fill_first_visible(
        page,
        [
            "input[placeholder*='Expiration' i]",
            "input[name*='expiry' i]",
            "input[name*='expiration' i]",
            "input[autocomplete='cc-exp']",
        ],
        expiry,
        "PayPal 有效期",
    )
    await human_delay(0.5, 1)

    await fill_first_visible(
        page,
        [
            "input[placeholder*='CVV' i]",
            "input[placeholder*='CVC' i]",
            "input[name*='cvv' i]",
            "input[name*='cvc' i]",
            "input[name*='securityCode' i]",
            "input[autocomplete='cc-csc']",
        ],
        cvv,
        "PayPal CVV",
    )
    await human_delay(0.5, 1)

    await fill_first_visible(
        page,
        [
            "input[placeholder*='First name' i]",
            "input[name*='firstName' i]",
            "input[autocomplete='given-name']",
        ],
        first_name,
        "PayPal First name",
    )
    await fill_first_visible(
        page,
        [
            "input[placeholder*='Last name' i]",
            "input[name*='lastName' i]",
            "input[autocomplete='family-name']",
        ],
        last_name,
        "PayPal Last name",
    )

    address = card_info.get("address", "")
    if address:
        await fill_paypal_billing_address(page, address)

    update_state(
        step="paypal_card_prefilled",
        paypal_card_number_masked=mask_value(card_number),
        paypal_expiry=expiry,
        paypal_billing_name=name,
        paypal_billing_address=address,
    )
    logger.info("✅ 已填写 PayPal 卡片和账单地址信息")

async def fill_paypal_billing_address(page, address):
    parts = split_us_address(address)
    street_selectors = [
        "input[placeholder='Street address']",
        "input[placeholder*='Street address' i]",
        "input[aria-label*='Street address' i]",
        "input[autocomplete='address-line1']",
        "input[name*='address1' i]",
        "input[name*='line1' i]",
    ]
    address_box = None
    for selector in street_selectors:
        candidate = page.locator(selector).first
        try:
            await candidate.wait_for(state="visible", timeout=3000)
            address_box = candidate
            break
        except Exception:
            continue

    if address_box is None:
        logger.warning("没有找到 PayPal Street address 输入框")
        return

    try:
        selected = await choose_address_autocomplete(page, address_box, address)
        if selected:
            logger.info("PayPal 地址自动补全已选中，继续补齐 City/State/ZIP")
        else:
            await address_box.fill(address, timeout=10000)
            logger.info("PayPal 地址未出现自动补全项，已在 Street address 填写完整地址")
    except Exception as e:
        logger.warning(f"PayPal 地址自动填写失败，改为 Street address 直接填写完整地址: {e}")
        await address_box.fill(address, timeout=10000)

    try:
        current_street = (await address_box.input_value(timeout=1000)).strip()
        if parts["street"] and current_street != parts["street"]:
            await address_box.fill(parts["street"], timeout=10000)
            logger.info("已修正 PayPal Street address，只保留街道部分")
    except Exception as e:
        logger.warning(f"PayPal Street address 修正失败，继续补齐城市/州/邮编: {e}")

    if parts["city"]:
        await fill_first_visible(
            page,
            [
                "input[autocomplete='address-level2']",
                "input[name*='city' i]",
                "input[name*='locality' i]",
                "input[placeholder*='City' i]",
                "input[aria-label*='City' i]",
            ],
            parts["city"],
            "PayPal 城市",
        )
    if parts["postal_code"]:
        await fill_first_visible(
            page,
            [
                "input[autocomplete='postal-code']",
                "input[name*='postal' i]",
                "input[placeholder*='ZIP' i]",
                "input[placeholder*='Postal' i]",
                "input[aria-label*='ZIP' i]",
                "input[aria-label*='Postal' i]",
            ],
            parts["postal_code"],
            "PayPal ZIP",
        )
    await fill_paypal_state_if_empty(page, parts)

async def fill_paypal_state_if_empty(page, parts):
    state = parts.get("state", "")
    state_name = parts.get("state_name", "")
    if not state:
        logger.warning("没有从地址/ZIP 推断出州，跳过 PayPal State 自动选择")
        return

    select_locator = page.locator(
        "select[autocomplete='address-level1'], select[name*='state' i], select[aria-label*='State' i]"
    ).first
    try:
        await select_locator.wait_for(state="visible", timeout=2500)
        current = await select_locator.input_value(timeout=1000)
        if current.strip():
            logger.info("PayPal State 已存在，继续")
            return
        try:
            await select_locator.select_option(value=state, timeout=3000)
        except Exception:
            await select_locator.select_option(label=state_name, timeout=3000)
        logger.info(f"已选择 PayPal State: {state_name or state}")
        return
    except Exception:
        pass

    state_field = None
    for selector in [
        "input[placeholder*='State' i]",
        "button:has-text('State')",
        "[role='button']:has-text('State')",
        "[aria-label*='State' i]",
        "[role='combobox']:has-text('State')",
        "text=State",
    ]:
        candidate = page.locator(selector).first
        try:
            await candidate.wait_for(state="visible", timeout=1500)
            state_field = candidate
            break
        except Exception:
            continue
    if state_field is None:
        logger.warning("没有找到 PayPal State 输入框/下拉框")
        return

    try:
        field_text = (await state_field.inner_text(timeout=1000)).strip()
        try:
            field_value = (await state_field.input_value(timeout=1000)).strip()
        except Exception:
            field_value = ""
        if state in field_text or state_name in field_text or field_value:
            logger.info("PayPal State 已存在，继续")
            return
        await state_field.click(timeout=5000)
        await human_delay(0.5, 1)
        option = page.locator(
            f"[role='option']:has-text('{state_name}'), li:has-text('{state_name}'), text={state_name}"
        ).first
        try:
            await option.click(timeout=5000)
        except Exception:
            option = page.locator(f"[role='option']:has-text('{state}'), li:has-text('{state}'), text={state}").first
            await option.click(timeout=5000)
        logger.info(f"已选择 PayPal State: {state_name or state}")
    except Exception as e:
        logger.warning(f"PayPal State 自动选择失败: {e}")

async def fill_checkout_billing_address_details(page, parts):
    if parts["city"]:
        await fill_first_visible(
            page,
            [
                "input[autocomplete='address-level2']",
                "input[name='locality']",
                "input[name*='city' i]",
                "input[placeholder='City']",
                "input[placeholder*='City' i]",
                "input[aria-label*='City' i]",
            ],
            parts["city"],
            "付款页 City",
        )
    if parts["postal_code"]:
        await fill_first_visible(
            page,
            [
                "input[autocomplete='postal-code']",
                "input[name='postalCode']",
                "input[name*='postal' i]",
                "input[placeholder='ZIP']",
                "input[placeholder*='ZIP' i]",
                "input[placeholder*='Postal' i]",
                "input[aria-label*='ZIP' i]",
                "input[aria-label*='Postal' i]",
            ],
            parts["postal_code"],
            "付款页 ZIP",
        )
    await fill_paypal_state_if_empty(page, parts)

async def is_paypal_phone_code_page(page):
    await handle_paypal_risk_page(page)
    selectors = [
        "text=Enter your code",
        "text=We sent a 6-digit code",
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric']",
    ]
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=700)
            return True
        except Exception:
            continue
    return False

async def wait_for_paypal_phone_code_manual_completion(page, timeout=180):
    logger.info("等待手动输入 PayPal 手机验证码；检测到页面离开验证码框后自动继续")
    update_state(step="paypal_phone_code_manual_wait")
    start = time.time()
    while time.time() - start < timeout:
        await human_delay(1, 2)
        if page.is_closed():
            logger.info("PayPal 验证码页已关闭，继续后续检测")
            update_state(step="paypal_phone_code_page_closed")
            return
        await handle_paypal_risk_page(page)
        if not await is_paypal_phone_code_page(page):
            logger.info("✅ 检测到 PayPal 手机验证码已手动完成，继续下一步")
            update_state(step="paypal_phone_code_manual_completed", paypal_url=page.url)
            return

    logger.warning("等待手动验证码自动检测超时，退回 Enter 兜底继续")
    await asyncio.to_thread(
        input,
        "如果你已经手动完成 PayPal 手机验证码，请按 Enter 继续..."
    )
    update_state(step="paypal_phone_code_manual_enter_fallback", paypal_url=page.url)

async def create_paypal_account_and_open_sms(page, phone_info, max_attempts=6):
    paypal_password = random_paypal_password()
    filled_password = await fill_first_visible(
        page,
        [
            "input[placeholder*='Create password' i]",
            "input[type='password']",
            "input[name*='password' i]",
            "input[autocomplete='new-password']",
        ],
        paypal_password,
        "PayPal 创建密码",
    )
    if not filled_password:
        logger.warning("没有找到 PayPal 创建密码输入框")

    update_state(paypal_password=paypal_password)
    await human_delay(1, 2)

    selectors = [
        "button:has-text('Agree & Create Account')",
        "button:has-text('Agree and Create Account')",
        "input[value='Agree & Create Account']",
        "input[value='Agree and Create Account']",
        "text=Agree & Create Account",
    ]
    for attempt in range(1, max_attempts + 1):
        await handle_paypal_risk_page(page)
        logger.info(f"点击 Agree & Create Account，第 {attempt} 次尝试...")
        await human_click_first_visible(page, selectors, "Agree & Create Account")
        for _ in range(30):
            await human_delay(0.8, 1.2)
            await handle_paypal_risk_page(page)
            if await is_paypal_phone_code_page(page):
                logger.info("✅ 已进入 PayPal 手机验证码页面")
                await open_paypal_sms_link(page, phone_info.get("sms_url", ""))
                await wait_for_paypal_phone_code_manual_completion(page)
                return
        logger.warning("点击 Agree & Create Account 后 30 秒内未进入验证码页，准备重试")

    raise Exception(f"点击 Agree & Create Account 后未进入验证码页，已重试 {max_attempts} 次")

async def open_paypal_sms_link(page, sms_url):
    if not sms_url:
        logger.warning("phone.txt 里没有短信链接，无法打开接码页面")
        return None
    sms_page = await page.context.new_page()
    await sms_page.goto(sms_url, wait_until="domcontentloaded", timeout=60000)
    await human_delay(1, 2)
    update_state(paypal_sms_page_url=sms_url)
    logger.info("✅ 已打开 PayPal 手机验证码短信链接")
    return sms_page

async def wait_for_plus_subscription_success(context, email_address, timeout=240):
    success_patterns = [
        "You're now subscribed to ChatGPT Plus",
        "You’re now subscribed to ChatGPT Plus",
        "now subscribed to ChatGPT Plus",
        "已成功订阅",
        "成功订阅",
    ]
    deadline = time.time() + timeout

    while time.time() < deadline:
        for candidate in list(context.pages):
            try:
                text = await candidate.locator("body").inner_text(timeout=3000)
            except Exception:
                text = ""
            url = candidate.url
            if any(pattern.lower() in text.lower() for pattern in success_patterns):
                await candidate.bring_to_front()
                update_state(
                    step="plus_subscription_success",
                    email=email_address,
                    success_url=url,
                )
                logger.info("✅ 检测到已成功订阅 ChatGPT Plus")
                return candidate
            if "/payments/success" in url:
                await candidate.bring_to_front()
                update_state(
                    step="plus_subscription_success_url",
                    email=email_address,
                    success_url=url,
                )
                logger.info("✅ 检测到 ChatGPT Plus 订阅成功页面")
                return candidate
        await asyncio.sleep(2)

    update_state(step="plus_subscription_success_not_found", email=email_address)
    raise Exception(f"{timeout} 秒内未检测到 ChatGPT Plus 订阅成功提示")

async def extract_paypal_code_from_sms(sms_page, max_wait=90):
    """从短信页面自动提取 6 位 PayPal 验证码"""
    for _ in range(max_wait // 3):
        try:
            content = await sms_page.content()
            match = re.search(r'PayPal[:\s]*(\d{6})', content, re.IGNORECASE)
            if match:
                return match.group(1)
            matches = re.findall(r'\b(\d{6})\b', content)
            if matches:
                return matches[-1]
        except Exception:
            pass
        await human_delay(3, 4)
    return None


async def fill_paypal_6_digit_code(page, code):
    """自动填充 PayPal 6 个独立数字框（优化版）"""
    if not code or len(code) != 6:
        return False

    inputs = page.locator("input[maxlength='1']:visible, input[inputmode='numeric'][maxlength='1']:visible")
    count = await inputs.count()

    if count >= 6:
        for i in range(6):
            await inputs.nth(i).fill(code[i])
            await human_delay(0.05, 0.12)
        logger.info(f"✅ 已自动填充验证码: {code}")
        return True

    logger.warning(f"只检测到 {count} 个验证码输入框，自动填充失败")
    return False

async def is_paypal_after_subscribe_page(page):
    current_url = page.url.lower()
    if "paypal." in current_url:
        return True

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    if "paypal." in page.url.lower():
        return True

    selectors = [
        "button:has-text('Create an Account')",
        "a:has-text('Create an Account')",
        "button:has-text('Continue to Payment')",
        "input[value='Continue to Payment']",
        "text=Pay with debit or credit card",
        "input[placeholder*='Email or mobile' i]",
        "input[placeholder*='Enter email' i]",
    ]
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=700)
            return True
        except Exception:
            continue
    return False

async def is_subscribe_redirect_in_progress(page):
    current_url = page.url.lower()
    return (
        "pm-redirects.stripe.com" in current_url
        or "stripe.com/authorize" in current_url
        or "paypal.com/agreements" in current_url
    )

async def click_subscribe_until_paypal_page(payment_page, max_attempts=6):
    subscribe_button = payment_page.locator("button:has-text('Subscribe')").first

    for attempt in range(1, max_attempts + 1):
        pages_before = set(payment_page.context.pages)
        logger.info(f"点击 Subscribe，第 {attempt} 次尝试...")

        try:
            if await is_paypal_after_subscribe_page(payment_page):
                await handle_paypal_risk_page(payment_page)
                logger.info("✅ 已经进入 PayPal 下一页，无需再次点击 Subscribe")
                return payment_page
            if await is_subscribe_redirect_in_progress(payment_page):
                logger.info("Subscribe 后正在 Stripe/PayPal 跳转中，继续等待")
            else:
                await subscribe_button.wait_for(state="visible", timeout=5000)
                await subscribe_button.scroll_into_view_if_needed(timeout=5000)
                await human_delay(0.5, 1)
                await subscribe_button.click(timeout=8000)
        except Exception as e:
            if await is_paypal_after_subscribe_page(payment_page):
                await handle_paypal_risk_page(payment_page)
                logger.info("✅ Subscribe 点击后已进入 PayPal 下一页")
                return payment_page
            if await is_subscribe_redirect_in_progress(payment_page):
                logger.info("Subscribe 点击后已进入跳转链路，继续等待")
            else:
                logger.warning(f"Subscribe 普通点击失败，改用 force click: {e}")
                await subscribe_button.click(force=True, timeout=5000)

        for _ in range(15):
            await human_delay(0.8, 1.2)
            pages = list(payment_page.context.pages)
            candidates = [candidate for candidate in pages if candidate not in pages_before]
            candidates.append(payment_page)
            for candidate in reversed(candidates):
                try:
                    if await is_paypal_after_subscribe_page(candidate):
                        await handle_paypal_risk_page(candidate)
                        logger.info("✅ Subscribe 后已进入 PayPal 下一页")
                        return candidate
                    if await is_subscribe_redirect_in_progress(candidate):
                        logger.info("Subscribe 后仍在 Stripe/PayPal 跳转中")
                except Exception:
                    continue

        if await is_subscribe_redirect_in_progress(payment_page):
            logger.warning("Subscribe 后仍在跳转链路中，继续等待而不是重复点击")
            continue

        logger.warning("Subscribe 后 15 秒内未进入 PayPal 下一页，准备重试")

    for _ in range(60):
        await human_delay(0.8, 1.2)
        if await is_paypal_after_subscribe_page(payment_page):
            await handle_paypal_risk_page(payment_page)
            logger.info("✅ Subscribe 后延长等待进入 PayPal 下一页")
            return payment_page

    raise Exception(f"点击 Subscribe 后未进入 PayPal 下一页，已重试 {max_attempts} 次")

async def fill_paypal_checkout(payment_page, card_info):
    logger.info("开始填写 PayPal 订阅页...")
    await payment_page.bring_to_front()
    await payment_page.wait_for_load_state("domcontentloaded", timeout=60000)
    await human_delay(3, 5)

    await select_paypal_method(payment_page)

    name = card_info.get("name") or random_full_name()
    await fill_first_visible(
        payment_page,
        [
            "input[name='name']",
            "input[autocomplete='name']",
            "input[placeholder='Name']",
            "input[aria-label='Name']",
            "input[aria-label*='Name']",
        ],
        name,
        "姓名",
    )

    address = card_info.get("address", "")
    if address:
        parts = split_us_address(address)
        address_box = payment_page.locator(
            "input[placeholder='Address'], input[autocomplete='address-line1'], input[name='addressLine1'], input[aria-label*='Address']"
        ).first
        try:
            await address_box.wait_for(state="visible", timeout=8000)
            selected = await choose_address_autocomplete(payment_page, address_box, address)
            if not selected:
                await address_box.fill(address, timeout=10000)
                logger.info("多次未出现地址自动补全项，已直接填写地址")
                if await click_if_visible(payment_page, ["text=Enter address manually", "button:has-text('Enter address manually')"], "手动地址", timeout=3000):
                    await fill_first_visible(payment_page, ["input[autocomplete='address-line1']", "input[name='addressLine1']", "input[placeholder*='Address']"], parts["street"], "街道")
                    if parts["city"]:
                        await fill_first_visible(payment_page, ["input[autocomplete='address-level2']", "input[name='locality']", "input[placeholder*='City']"], parts["city"], "城市")
                    if parts["postal_code"]:
                        await fill_first_visible(payment_page, ["input[autocomplete='postal-code']", "input[name='postalCode']", "input[placeholder*='ZIP']", "input[placeholder*='Postal']"], parts["postal_code"], "邮编")
            await fill_checkout_billing_address_details(payment_page, parts)
        except Exception as e:
            logger.warning(f"地址自动填写失败，尝试手动地址: {e}")
            if await click_if_visible(payment_page, ["text=Enter address manually", "button:has-text('Enter address manually')"], "手动地址", timeout=5000):
                await fill_first_visible(payment_page, ["input[autocomplete='address-line1']", "input[name='addressLine1']", "input[placeholder*='Address']"], parts["street"], "街道")
                if parts["city"]:
                    await fill_first_visible(payment_page, ["input[autocomplete='address-level2']", "input[name='locality']", "input[placeholder*='City']"], parts["city"], "城市")
                if parts["postal_code"]:
                    await fill_first_visible(payment_page, ["input[autocomplete='postal-code']", "input[name='postalCode']", "input[placeholder*='ZIP']", "input[placeholder*='Postal']"], parts["postal_code"], "邮编")
            await fill_checkout_billing_address_details(payment_page, parts)

    await check_subscription_terms(payment_page)

    update_state(
        step="checkout_prefilled",
        checkout_url=payment_page.url,
        billing_name=name,
        billing_address=address,
        card_number_masked=mask_value(card_info.get("card_number", "")),
    )

    logger.info("付款页已预填，自动点击 Subscribe...")
    payment_page = await click_subscribe_until_paypal_page(payment_page)

    update_state(step="subscribe_clicked", checkout_url=payment_page.url)
    logger.info("✅ 已点击 Subscribe")
    return payment_page

async def redeem_cardmi(context, email_address):
    manual_card_info = load_manual_card_info()
    if manual_card_info:
        logger.info(f"manual_card_info.json enabled, skip cardmi redeem and use manual virtual card: {mask_value(manual_card_info.get('card_number', ''))}")
        update_state(
            step="manual_card_info_loaded",
            email=email_address,
            card_number_masked=mask_value(manual_card_info.get("card_number", "")),
        )
        return None, manual_card_info

    pending = get_pending_card()
    if pending:
        card_info = pending["card_info"]
        logger.info(f"Reuse pending virtual card from previous unfinished account: {mask_value(card_info.get('card_number', ''))}")
        update_state(
            step="pending_card_reused",
            email=email_address,
            cardmi_masked=pending.get("cardmi_masked", ""),
            card_number_masked=mask_value(card_info.get("card_number", "")),
        )
        return None, card_info

    card_page = await context.new_page()
    await card_page.goto("http://card.778.chat/", wait_until="networkidle", timeout=60000)
    await human_delay(2, 4)
    await click_if_visible(
        card_page,
        [
            "button:has-text('\u6211\u77e5\u9053\u4e86')",
            "text=\u6211\u77e5\u9053\u4e86",
        ],
        "cardmi notice popup",
        timeout=5000,
    )
    await human_delay(1, 2)

    textarea = card_page.locator("textarea")
    if await textarea.count() > 0:
        card_input = textarea.first
    else:
        card_input = card_page.locator("input").first
    await card_input.wait_for(state="visible", timeout=30000)

    failure_words = ("\u5931\u8d25", "\u9519\u8bef", "\u65e0\u6548", "\u5df2\u4f7f\u7528", "expired", "failed", "error", "invalid", "used")
    while True:
        cardmi = select_cardmi()
        update_state(step="cardmi_redeeming", email=email_address, cardmi_masked=mask_value(cardmi))
        logger.info(f"Open cardmi redeem page, trying cardmi: {mask_value(cardmi)}")

        await card_input.fill(cardmi, timeout=30000)
        await human_delay(1, 2)

        await human_click(card_page, "button:has-text('\u6279\u91cf\u5151\u6362')")
        await human_delay(6, 10)

        body_text = await card_page.locator("body").inner_text(timeout=10000)
        card_info = extract_virtual_card_info(body_text)
        body_text_lower = body_text.lower()
        redeem_failed = not card_info["card_number"] and any(word in body_text_lower for word in failure_words)
        if redeem_failed:
            Path("card_debug.txt").write_text(body_text[:4000], encoding="utf-8")
            update_state(step="cardmi_failed_try_next", cardmi_masked=mask_value(cardmi))
            logger.warning(f"Cardmi redeem failed; mark it and clear input before trying next: {mask_value(cardmi)}")
            mark_cardmi_used(cardmi)
            await click_if_visible(
                card_page,
                [
                    "button:has-text('\u6e05\u7a7a')",
                    "text=\u6e05\u7a7a",
                ],
                "cardmi clear button",
                timeout=5000,
            )
            await human_delay(0.5, 1)
            await card_input.fill("", timeout=30000)
            continue

        if not card_info["card_number"]:
            Path("card_debug.txt").write_text(body_text[:4000], encoding="utf-8")
            update_state(step="cardmi_no_card_info", cardmi_masked=mask_value(cardmi))
            raise Exception("Cardmi redeemed but no virtual card info was parsed; saved card_debug.txt")

        record = {
            "email": email_address,
            "cardmi": cardmi,
            "cardmi_masked": mask_value(cardmi),
            "redeemed_at": now_text(),
            "page_url": card_page.url,
            "card_info": card_info,
        }
        append_jsonl(CARD_RESULT_FILE, record)
        save_pending_card(cardmi, card_info, email_address)
        update_state(
            step="cardmi_redeemed_pending",
            email=email_address,
            cardmi_masked=mask_value(cardmi),
            card_result_file=str(CARD_RESULT_FILE),
        )
        logger.info("Cardmi redeem completed; virtual card saved as pending and cardmi will be marked used after account success")
        return card_page, card_info

def get_latest_inbox_uid():
    if is_outlook_provider():
        return 0
    EMAIL = CONFIG["gmail"]["email"]
    PASSWORD = CONFIG["gmail"]["app_password"]

    last_error = None
    for _ in range(3):
        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL, PASSWORD)
            mail.select("INBOX")
            result, data = mail.uid("search", None, "ALL")
            if result == "OK" and data and data[0]:
                return int(data[0].split()[-1])
            return 0
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, EOFError, ssl.SSLError) as e:
            last_error = e
            logger.warning(f"读取邮箱 UID 基线失败，重试: {e}")
            time.sleep(2)
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass

    raise Exception(f"无法读取邮箱 UID 基线: {last_error}")


def get_latest_verification_code(start_time=None, target_email=None, timeout_seconds=60, after_uid=None):
    if is_outlook_provider():
        raise Exception("当前为 Outlook 手动验证码模式，不自动读取邮件验证码")
    EMAIL = CONFIG["gmail"]["email"]
    PASSWORD = CONFIG["gmail"]["app_password"]
    start_time = start_time or time.time()
    target_email_lower = (target_email or "").lower()

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

    def get_internal_date(fetch_data):
        from email.utils import parsedate_to_datetime
        for item in fetch_data:
            if not isinstance(item, tuple):
                continue
            header = item[0]
            if isinstance(header, bytes):
                header = header.decode("utf-8", errors="replace")
            match = re.search(r'INTERNALDATE "([^"]+)"', header)
            if match:
                return parsedate_to_datetime(match.group(1)).timestamp()
        return None

    def get_body(msg):
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get_content_type() in ["text/plain", "text/html"]:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body += payload.decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace"
                        )
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                body = payload.decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace"
                )
        return body

    def extract_code(text):
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        candidates = []
        for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text):
            start, end = match.span()
            before = text[max(0, start - 160):start].lower()
            after = text[end:end + 160].lower()
            context = before + after
            score = 0
            if re.search(r"(verification|temporary|security|login|sign.?in|one.?time)\s+code", before):
                score += 6
            if "code" in before[-80:]:
                score += 3
            if any(word in context for word in ("openai", "chatgpt", "expires", "minute", "verify")):
                score += 2
            if any(word in context for word in ("copyright", "privacy", "unsubscribe", "address")):
                score -= 4
            candidates.append((score, start, match.group(1)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        return candidates[0][2]

    def is_target_message(msg, body):
        if not target_email_lower:
            return True
        header_text = " ".join(
            str(msg.get(header, ""))
            for header in ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")
        ).lower()
        return target_email_lower in header_text or target_email_lower in body[:2000].lower()

    def looks_like_code_email(subject, body):
        text = f"{subject}\n{body}".lower()
        keywords = (
            "chatgpt",
            "openai",
            "verification",
            "verify",
            "temporary code",
            "security code",
            "验证码",
        )
        return any(keyword in text for keyword in keywords)

    print(f"等待新验证码邮件到达...（最多等 {timeout_seconds} 秒）")

    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL, PASSWORD)
            mail.select("INBOX")
            result, data = mail.uid("search", None, "ALL")
            if result == "OK" and data and data[0]:
                email_ids = data[0].split()[-120:]
                fallback_code = None

                for eid in reversed(email_ids):
                    if after_uid is not None and int(eid) <= after_uid:
                        continue

                    result, msg_data = mail.uid("fetch", eid, "(INTERNALDATE BODY.PEEK[])")
                    if result != "OK":
                        continue

                    received_time = get_internal_date(msg_data)
                    if received_time is not None and received_time < start_time - 300:
                        continue

                    raw = None
                    for item in msg_data:
                        if isinstance(item, tuple) and item[1]:
                            raw = item[1]
                            break
                    if raw is None:
                        continue

                    msg = email.message_from_bytes(raw)
                    subject = decode_subject(msg["Subject"]).strip()
                    body = get_body(msg)

                    if not looks_like_code_email(subject, body):
                        continue

                    code = extract_code(f"{subject}\n{body}")
                    if not code:
                        continue

                    if is_target_message(msg, body):
                        logger.info(f"Matched verification email subject: {subject}")
                        return code

                    if fallback_code is None:
                        fallback_code = code

                if fallback_code is not None:
                    logger.info("Matched newest verification email without target-recipient header")
                    return fallback_code

        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, EOFError, ssl.SSLError) as e:
            last_error = e
            logger.warning(f"IMAP 连接中断，继续重试: {e}")
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass

        time.sleep(2)

    if last_error is not None:
        raise Exception(f"{timeout_seconds} 秒内未收到新验证码邮件，最后一次 IMAP 错误: {last_error}")
    raise Exception(f"{timeout_seconds} 秒内未收到新验证码邮件")

async def wait_for_manual_chatgpt_code(page, timeout_seconds=60):
    logger.info(f"等待你手动输入 ChatGPT 邮箱验证码，最多 {timeout_seconds} 秒；检测到已输入后自动继续")
    update_state(step="chatgpt_code_manual_wait", manual_code_timeout=timeout_seconds)
    selectors = [
        "input[placeholder*='Code' i]",
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric']",
    ]
    saw_code_page = False
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        values = []
        visible_count = 0
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            for index in range(min(count, 8)):
                item = locator.nth(index)
                try:
                    if not await item.is_visible(timeout=300):
                        continue
                    visible_count += 1
                    values.append(await item.input_value(timeout=500))
                except Exception:
                    continue
        if visible_count:
            saw_code_page = True
            digits = re.sub(r"\D", "", "".join(values))
            if len(digits) >= 6:
                logger.info("✅ 检测到邮箱验证码已手动输入，继续点击 Continue")
                update_state(step="chatgpt_code_manual_entered")
                return True
        elif saw_code_page:
            logger.info("✅ 验证码输入框已消失，判断已手动完成验证")
            update_state(step="chatgpt_code_manual_completed")
            return False
        await asyncio.sleep(1)
    raise Exception(f"{timeout_seconds} 秒内未检测到你手动输入邮箱验证码")


async def get_chatgpt_code_from_bridge_or_manual(page, email_address, timeout_seconds=60):
    logger.info(f"优先从本地验证码桥接层读取验证码，邮箱: {email_address}")
    update_state(
        step="chatgpt_code_bridge_wait",
        verification_code_bridge_mode=CONFIG["verification_code_bridge"].get("mode"),
        verification_code_timeout=timeout_seconds,
    )
    try:
        code = await asyncio.to_thread(wait_for_local_code, email_address, CONFIG, timeout_seconds)
        logger.info(f"✅ 本地验证码桥接层已返回验证码: {code}")
        update_state(step="verification_code_obtained", email=email_address, verification_code_source="bridge")
        await human_type(page, "input[placeholder*='Code']", code)
        await human_delay(1, 2)
        await human_click(page, "button:has-text('Continue')")
        await human_delay(4, 7)
        return
    except Exception as e:
        logger.warning(f"本地验证码桥接层未取到验证码，回退到手动输入检测: {e}")
        update_state(step="chatgpt_code_bridge_failed_fallback_manual", error=str(e))

    should_click_continue = await wait_for_manual_chatgpt_code(page, timeout_seconds=timeout_seconds)
    if should_click_continue:
        await human_click(page, "button:has-text('Continue')")
        await human_delay(4, 7)

# ==================== 主流程 ====================
async def process_one_account():
    profile_id = CONFIG["roxy_profile_id"]
    logger.info(f"=== 开始处理新账号 | Profile: {profile_id} ===")

    ws_endpoint = launch_roxy_profile(profile_id)
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_endpoint)
        context = await browser.new_context()
        await context.clear_cookies()
        page = await context.new_page()

        email_address = generate_email_address()
        logger.info(f"使用邮箱: {email_address}")
        update_state(step="account_started", email=email_address, started_at=now_text())

        try:
            if is_outlook_provider():
                await open_outlook_for_manual_code(context, email_address)
                await page.bring_to_front()

            await page.goto("https://chatgpt.com/", timeout=60000)
            await human_delay(5, 8)

            await human_click(page, "text=Sign up for free")
            await human_delay(3, 6)

            # 输入邮箱
            await human_type(page, "input[placeholder='Email address']", email_address)
            await human_delay(1, 2)

            # 点击第一个 Continue（进入验证码页面）
            last_email_uid = get_latest_inbox_uid()
            if not is_outlook_provider():
                logger.info(f"邮箱 UID 基线: {last_email_uid}")
            code_start_time = time.time()   # ← 在点击之前记录时间
            await human_click(page, "button:has-text('Continue'):not(:has-text('with'))")
            await human_delay(4, 7)

            logger.info("已进入验证码页面，开始填写...")

            if is_outlook_provider():
                await get_chatgpt_code_from_bridge_or_manual(page, email_address, timeout_seconds=60)
            else:
                # 获取验证码（传入开始时间）
                code = get_latest_verification_code(code_start_time, email_address, after_uid=last_email_uid)
                logger.info(f"获取到验证码: {code}")
                update_state(step="verification_code_obtained", email=email_address)
                
                # 输入验证码
                await human_type(page, "input[placeholder*='Code']", code)
                await human_delay(1, 2)

                # 点击第二个 Continue
                await human_click(page, "button:has-text('Continue')")
                await human_delay(4, 7)

            logger.info("已进入填写资料页面...")

            # 填写 Full name（用英文名）
            full_name = random_full_name()
            logger.info(f"随机姓名: {full_name}")
            await fill_profile_name_and_age(page, full_name)

            # 点击提交资料按钮：不同页面可能显示 Finish creating account 或 Continue
            await human_click_first_visible(
                page,
                [
                    "button:has-text('Finish creating account')",
                    "button:has-text('Continue')",
                ],
                "创建账号提交按钮"
            )
            await human_delay(6, 10)

            logger.info("✅ 注册完成，等待获取 token...")
            token = await fetch_chatgpt_access_token(page)
            logger.info("✅ Phase 1 完成，获取到 token")
            update_state(step="token_obtained", email=email_address, token_masked=mask_value(token, keep=10))
            append_account_status(email_address, "Free")

            payment_page = await generate_payment_link(page, token, email_address)
            update_state(step="payment_link_opened", email=email_address, payment_page_url=payment_page.url)
            await assert_total_due_today_zero(payment_page, email_address)

            card_page, card_info = await redeem_cardmi(context, email_address)
            await payment_page.bring_to_front()
            update_state(
                step="returned_to_payment_page_after_cardmi",
                email=email_address,
                payment_page_url=payment_page.url,
                card_number_masked=mask_value(card_info.get("card_number", "")),
            )
            payment_page = await fill_paypal_checkout(payment_page, card_info)
            payment_page = await prepare_paypal_account_payment_page(payment_page, email_address, card_info)
            finish_pending_card()
            append_account_status(email_address, "Plus")
            logger.info("Account subscribed to Plus. Waiting 5 seconds before restarting browser.")
            await asyncio.sleep(5)
            return True

            await payment_page.bring_to_front()
            logger.info("已进入 PayPal 支付信息页，最后页面已保留，查看完成后回到终端按 Enter 结束脚本...")
            await asyncio.to_thread(input, "已进入 PayPal 支付信息页，最后页面已保留，查看完成后按 Enter 结束脚本...")

            logger.info("✅ 账号处理完成！")

        except RestartAccountRequired as e:
            logger.warning(f"当前账号需要重开: {e}")
            update_state(step="restart_account_required", email=email_address, error=str(e))
            try:
                await page.screenshot(path=screenshot_file("restart", "restart"), full_page=True)
            except Exception:
                pass
            raise
        except PayPalBlocked as e:
            logger.error(f"PayPal blocked，关闭浏览器并停止当前自动化: {e}")
            update_state(step="paypal_blocked_stop", email=email_address, error=str(e))
            return False
        except Exception as e:
            logger.error(f"❌ 出错: {e}")
            update_state(step="error", email=email_address, error=str(e))
            await page.screenshot(path=screenshot_file("error", "error"))
            return False
        finally:
            await browser.close()

async def main():
    logger.info("=== GPT Plus 自动化脚本启动 ===")
    validate_config()
    restart_count = 0
    success_count = 0
    while True:
        try:
            ok = await process_one_account()
            if not ok:
                break
            success_count += 1
            update_state(step="account_success_loop", success_count=success_count)
            logger.info(f"已完成 {success_count} 个账号，准备继续创建下一个账号")
            await asyncio.sleep(random.uniform(5, 10))
        except RestartAccountRequired as e:
            restart_count += 1
            logger.warning(f"准备重新注册账号，第 {restart_count} 次重开原因: {e}")
            update_state(step="account_restart_loop", restart_count=restart_count, error=str(e))
            await asyncio.sleep(random.uniform(5, 10))
    logger.info("=== 运行结束 ===")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
            input("=== 运行结束 ===\n按 Enter 重新开始，Ctrl+C 退出...")
        except KeyboardInterrupt:
            print("\n已退出。")
            break
