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

# Всі 12 адрес — кожен job отримує свій CHUNK через env var CHUNK (0,1,2,3)
ADDRESSES = [
    {"city": "с. Карапиші",    "street": "вул. Молодіжна 12",          "queue_code": "GPV1.1", "queue_name": "1.1"},
    {"city": "м. Богуслав",    "street": "вул. Теліги Олени",          "queue_code": "GPV1.2", "queue_name": "1.2"},
    {"city": "м. Біла Церква", "street": "вул. Гончара Олеся 2",       "queue_code": "GPV2.1", "queue_name": "2.1"},
    {"city": "м. Біла Церква", "street": "вул. Голуба Професора",      "queue_code": "GPV2.2", "queue_name": "2.2"},
    {"city": "м. Миронівка",   "street": "вул. Шевченка 2",            "queue_code": "GPV3.1", "queue_name": "3.1"},
    {"city": "м. Миронівка",   "street": "вул. Зеленого Мирона 13",    "queue_code": "GPV3.2", "queue_name": "3.2"},
    {"city": "м. Біла Церква", "street": "вул. Рибна 32",              "queue_code": "GPV4.1", "queue_name": "4.1"},
    {"city": "м. Біла Церква", "street": "вул. Шевченка 4",            "queue_code": "GPV4.2", "queue_name": "4.2"},
    {"city": "м. Біла Церква", "street": "вул. Героїв Небесної Сотні", "queue_code": "GPV5.1", "queue_name": "5.1"},
    {"city": "м. Біла Церква", "street": "вул. Глибочицька 18",        "queue_code": "GPV5.2", "queue_name": "5.2"},
    {"city": "м. Біла Церква", "street": "вул. Сухоярська 4",          "queue_code": "GPV6.1", "queue_name": "6.1"},
    {"city": "м. Вишневе",     "street": "вул. Гоголя 2",              "queue_code": "GPV6.2", "queue_name": "6.2"},
]

CHUNK_SIZE = 3  # адрес на job


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


def get_csrf(html: str) -> str | None:
    for pattern in [
        r'csrf-token" content="([^"]+)"',
        r'content="([^"]+)" name="csrf-token"',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def build_intervals(data: dict) -> list[tuple[int, int]]:
    intervals: list[list[int]] = []
    current: list[int] | None = None
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


def sorted_queues(queues: list[str]) -> str:
    try:
        return ", ".join(sorted(queues, key=lambda q: float(q)))
    except ValueError:
        return ", ".join(sorted(queues))


def format_schedule(off_groups: dict, all_queues: set) -> str:
    if not off_groups:
        return "🟢 Світло є до кінця доби"
    off_queues: set[str] = set()
    lines: list[str] = []
    for key, queues in sorted(off_groups.items(), key=lambda x: int(x[0].split("-")[0])):
        s, e = map(int, key.split("-"))
        off_queues.update(queues)
        lines.append(f"🔴 {format_time(s)}–{format_time(e)}  |  черги {sorted_queues(queues)}")
    on_queues = all_queues - off_queues
    if on_queues:
        lines.append(f"🟢 Без відключень: черги {sorted_queues(list(on_queues))}")
    return "\n".join(lines)


def load_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def save_file(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(value)


def load_reminders() -> set[str]:
    try:
        with open(REMINDER_FILE) as f:
            return set(f.read().splitlines())
    except FileNotFoundError:
        return set()


def save_reminder(key: str) -> None:
    with open(REMINDER_FILE, "a") as f:
        f.write(key + "\n")


# ── Fetch chunk ───────────────────────────────────────────────────────────────

def fetch_chunk(addresses: list) -> dict:
    """Запитує ДТЕК для списку адрес, повертає {queue_code: {hour: status}}"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
    })

    try:
        r1 = session.get(BASE_URL, timeout=20)
        r1.raise_for_status()
    except Exception as exc:
        log.error("Cannot reach DTEK: %s", exc)
        return {}

    csrf = get_csrf(r1.text)
    if not csrf:
        log.warning("CSRF not found. Response: %s", r1.text[:300])
        return {}

    headers_post = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          BASE_URL,
        "Origin":           "https://www.dtek-krem.com.ua",
        "X-CSRF-Token":     csrf,
    }

    now    = datetime.now(KYIV_TZ)
    result = {}

    for address in addresses:
        payload = {
            "method":         "getHomeNum",
            "data[0][name]":  "city",
            "data[0][value]": address["city"],
            "data[1][name]":  "street",
            "data[1][value]": address["street"],
            "data[2][name]":  "updateFact",
            "data[2][value]": now.strftime("%H:%M %d.%m.%Y"),
        }
        try:
            r2 = session.post(API_URL, data=payload, headers=headers_post, timeout=20)
            r2.raise_for_status()
            data = r2.json()
        except Exception as exc:
            log.warning("Request failed for %s: %s", address["queue_code"], exc)
            time.sleep(2)
            continue

        if "fact" not in data:
            log.warning("No 'fact' for %s. Response: %s", address["queue_code"], str(data)[:300])
            continue

        all_days   = data["fact"]["data"]
        timestamps = sorted(all_days.keys(), key=int)

        result[address["queue_code"]] = {
            "today":    all_days[timestamps[0]].get(address["queue_code"], {}),
            "tomorrow": all_days[timestamps[1]].get(address["queue_code"], {}) if len(timestamps) > 1 else {},
        }
        log.info("QUEUE %s | data: %s", address["queue_code"], result[address["queue_code"]]["today"])
        time.sleep(15)

    return result


# ── Collect mode (збираємо дані для одного chunk) ─────────────────────────────

def run_collect(chunk_index: int) -> None:
    start = chunk_index * CHUNK_SIZE
    chunk = ADDRESSES[start: start + CHUNK_SIZE]
    log.info("Collecting chunk %d: %s", chunk_index, [a["queue_code"] for a in chunk])

    data = fetch_chunk(chunk)

    out_file = f"chunk_{chunk_index}.json"
    with open(out_file, "w") as f:
        json.dump(data, f)
    log.info("Saved %s", out_file)


# ── Notify mode (читаємо всі chunk файли і надсилаємо повідомлення) ───────────

def run_notify() -> None:
    now         = datetime.now(KYIV_TZ)
    now_minutes = now.hour * 60 + now.minute
    today       = now.strftime("%Y-%m-%d")
    all_queue_names = {a["queue_name"] for a in ADDRESSES}

    # Збираємо дані з усіх chunk файлів
    all_data: dict = {}
    for i in range(4):
        path = f"chunk_{i}.json"
        try:
            with open(path) as f:
                all_data.update(json.load(f))
        except FileNotFoundError:
            log.warning("Missing %s — chunk may have failed", path)

    if not all_data:
        log.error("No chunk data found, aborting")
        return

    # Будуємо групи відключень
    queue_map = {a["queue_code"]: a["queue_name"] for a in ADDRESSES}
    off_groups:      dict[str, list[str]] = {}
    reminder_groups: dict[str, list[str]] = {}
    tomorrow_groups: dict[str, list[str]] = {}

    for code, days in all_data.items():
        queue_name = queue_map.get(code, code)

        for s, e in build_intervals(days.get("today", {})):
            if e > now_minutes:
                key = f"{s}-{e}"
                off_groups.setdefault(key, []).append(queue_name)
                if 55 <= (s - now_minutes) <= 65:
                    reminder_groups.setdefault(key, []).append(queue_name)

        for s, e in build_intervals(days.get("tomorrow", {})):
            tomorrow_groups.setdefault(f"{s}-{e}", []).append(queue_name)

    # Сьогоднішній графік
    schedule = format_schedule(off_groups, all_queue_names)
    final    = f"📊 Оновлено графік  ({now.strftime('%H:%M')})\n\n{schedule}"
    new_hash = hashlib.md5((today + final).encode()).hexdigest()

    if new_hash != load_file(STATE_FILE):
        if send_message(final):
            save_file(STATE_FILE, new_hash)
            log.info("Today's schedule updated")

    # Нагадування за 1 годину
    reminders = load_reminders()
    for key, queues in reminder_groups.items():
        rkey = f"{today}_{key}"
        if rkey in reminders:
            continue
        s, e = map(int, key.split("-"))
        text = (
            f"⚠️ Через 1 годину відключення світла\n\n"
            f"🔴 {format_time(s)}–{format_time(e)}\n"
            f"Черги: {sorted_queues(queues)}"
        )
        if send_message(text):
            save_reminder(rkey)

    # Завтрашній графік (після 18:00)
    if tomorrow_groups and now.hour >= 18:
        schedule_tmr  = format_schedule(tomorrow_groups, all_queue_names)
        tomorrow_text = f"🗓️ Графік на завтра\n\n{schedule_tmr}"
        thash         = hashlib.md5((today + tomorrow_text).encode()).hexdigest()
        if thash != load_file(STATE_TOMORROW):
            if send_message(tomorrow_text):
                save_file(STATE_TOMORROW, thash)
                log.info("Tomorrow's schedule updated")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = os.getenv("MODE", "collect")  # collect | notify

    if mode == "notify":
        log.info("=== NOTIFY mode ===")
        run_notify()
    else:
        chunk = int(os.getenv("CHUNK", "0"))
        log.info("=== COLLECT mode, chunk %d ===", chunk)
        run_collect(chunk)
