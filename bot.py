import requests
import re
import hashlib
import time
import os
import json
import logging

from datetime import datetime
from zoneinfo import ZoneInfo


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL  = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID   = "-1003802691352"

STATE_FILE     = "state.txt"
STATE_TOMORROW = "state_tomorrow.txt"
REMINDER_FILE  = "reminders.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")

# ── ADDRESSES ─────────────────────────────────────────────────────────────────

ADDRESSES = [

# {"city": "с. Карапиші",    "street": "вул. Молодіжна 12",          "queue_code": "GPV1.1", "queue_name": "1.1"},

{"city": "м. Богуслав",    "street": "вул. Теліги Олени",          "queue_code": "GPV1.2", "queue_name": "1.2"},

{"city": "м. Біла Церква", "street": "вул. Гончара Олеся 2",       "queue_code": "GPV2.1", "queue_name": "2.1"},

# {"city": "м. Біла Церква", "street": "вул. Голуба Професора",      "queue_code": "GPV2.2", "queue_name": "2.2"},
# {"city": "м. Миронівка",   "street": "вул. Шевченка 2",            "queue_code": "GPV3.1", "queue_name": "3.1"},
# {"city": "м. Миронівка",   "street": "вул. Зеленого Мирона 13",    "queue_code": "GPV3.2", "queue_name": "3.2"},
# {"city": "м. Біла Церква", "street": "вул. Рибна 32",              "queue_code": "GPV4.1", "queue_name": "4.1"},
# {"city": "м. Біла Церква", "street": "вул. Шевченка 4",            "queue_code": "GPV4.2", "queue_name": "4.2"},

{"city": "м. Біла Церква", "street": "вул. Героїв Небесної Сотні", "queue_code": "GPV5.1", "queue_name": "5.1"},

# {"city": "м. Біла Церква", "street": "вул. Глибочицька 18",        "queue_code": "GPV5.2", "queue_name": "5.2"},
# {"city": "м. Біла Церква", "street": "вул. Сухоярська 4",          "queue_code": "GPV6.1", "queue_name": "6.1"},
# {"city": "м. Вишневе",     "street": "вул. Гоголя 2",              "queue_code": "GPV6.2", "queue_name": "6.2"},
]

CHUNK_SIZE = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def send_message(text: str) -> bool:

    try:

        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=15,
        )

        r.raise_for_status()

        log.info("Message sent (%d chars)", len(text))

        return True

    except Exception as exc:

        log.error("Failed to send message: %s", exc)

        return False


def format_time(minutes: int) -> str:

    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_csrf(html: str):

    for pattern in [
        r'csrf-token" content="([^"]+)"',
        r'content="([^"]+)" name="csrf-token"',
    ]:

        m = re.search(pattern, html)

        if m:
            return m.group(1)

    return None


def build_intervals(data):

    intervals = []

    current = None

    for hour in range(1, 25):

        status = data.get(str(hour))

        if status in ("no", "first", "second"):

            start = (hour - 1) * 60
            end   = hour * 60

            if status == "first":
                end = start + 30

            elif status == "second":
                start += 30

            if current and start == current[1]:
                current[1] = end

            else:

                if current:
                    intervals.append(current)

                current = [start, end]

        else:

            if current:
                intervals.append(current)
                current = None

    if current:
        intervals.append(current)

    return [(s, e) for s, e in intervals]


def load_file(path):

    try:

        with open(path) as f:

            return f.read().strip() or None

    except FileNotFoundError:

        return None


def save_file(path, value):

    with open(path, "w") as f:

        f.write(value)


def load_reminders():

    try:

        with open(REMINDER_FILE) as f:

            return set(f.read().splitlines())

    except FileNotFoundError:

        return set()


def save_reminder(key):

    with open(REMINDER_FILE, "a") as f:

        f.write(key + "\n")


# ── Fetch data ────────────────────────────────────────────────────────────────

def fetch_data():

    session = requests.Session()

    r1 = session.get(BASE_URL)

    csrf = get_csrf(r1.text)

    if not csrf:

        log.error("CSRF not found")

        return {}

    headers_post = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf,
    }

    now = datetime.now(KYIV_TZ)

    result = {}

    for address in ADDRESSES:

        payload = {
            "method": "getHomeNum",
            "data[0][name]": "city",
            "data[0][value]": address["city"],
            "data[1][name]": "street",
            "data[1][value]": address["street"],
            "data[2][name]": "updateFact",
            "data[2][value]": now.strftime("%H:%M %d.%m.%Y"),
        }

        r2 = session.post(API_URL, data=payload, headers=headers_post)

        data = r2.json()

        if "fact" not in data:
            continue

        all_days = data["fact"]["data"]

        timestamps = sorted(all_days.keys(), key=int)

        result[address["queue_code"]] = {
            "today": all_days[timestamps[0]].get(address["queue_code"], {}),
            "tomorrow": all_days[timestamps[1]].get(address["queue_code"], {}) if len(timestamps) > 1 else {},
        }

        log.info("QUEUE %s loaded", address["queue_code"])

        time.sleep(15)

    return result


# ── Main process ──────────────────────────────────────────────────────────────

def process():

    data = fetch_data()

    if not data:
        return

    now = datetime.now(KYIV_TZ)

    now_minutes = now.hour * 60 + now.minute

    today = now.strftime("%Y-%m-%d")

    off_groups = {}

    reminder_groups = {}

    for code, days in data.items():

        for s, e in build_intervals(days["today"]):

            if e > now_minutes:

                key = f"{s}-{e}"

                off_groups.setdefault(key, []).append(code)

                if 55 <= (s - now_minutes) <= 65:

                    reminder_groups.setdefault(key, []).append(code)

    lines = []

    for key, queues in off_groups.items():

        s, e = map(int, key.split("-"))

        lines.append(f"🔴 {format_time(s)}–{format_time(e)} | черги {', '.join(queues)}")

    if not lines:

        schedule = "🟢 Світло є до кінця доби"

    else:

        schedule = "\n".join(lines)

    final = f"📊 Оновлено графік\n\n{schedule}"

    new_hash = hashlib.md5((today + final).encode()).hexdigest()

    if new_hash != load_file(STATE_FILE):

        if send_message(final):

            save_file(STATE_FILE, new_hash)

    reminders = load_reminders()

    for key, queues in reminder_groups.items():

        rkey = f"{today}_{key}"

        if rkey in reminders:
            continue

        s, e = map(int, key.split("-"))

        text = (
            "⚠️ Через 1 годину відключення світла\n\n"
            f"🔴 {format_time(s)}–{format_time(e)}\n"
            f"Черги: {', '.join(queues)}"
        )

        if send_message(text):

            save_reminder(rkey)


# ── LOOP ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    while True:

        try:

            log.info("CHECK GRAPH")

            process()

        except Exception as e:

            log.error("ERROR %s", e)

        time.sleep(900)
