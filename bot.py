import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

API_URL = "https://dtek-api.svitlo-proxy.workers.dev/"
REGION_ID = "kiivska-oblast"
STATE_FILE = "state.txt"
KYIV_TZ = ZoneInfo("Europe/Kyiv")
BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"
HTTP_TIMEOUT = 30

QUEUE_LABELS = {
    "1.1": "1.1", "1.2": "1.2",
    "2.1": "2.1", "2.2": "2.2",
    "3.1": "3.1", "3.2": "3.2",
    "4.1": "4.1", "4.2": "4.2",
    "5.1": "5.1", "5.2": "5.2",
    "6.1": "6.1", "6.2": "6.2",
}


def split_message(message, max_length=3500):
    """Split long Telegram messages on line boundaries, below Telegram's limit."""
    chunks = []
    current_lines = []
    current_length = 0

    for line in message.splitlines():
        line_length = len(line) + (1 if current_lines else 0)
        if current_lines and current_length + line_length > max_length:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_length = len(line)
        else:
            current_lines.append(line)
            current_length += line_length

    if current_lines:
        chunks.append("\n".join(current_lines))

    if any(len(chunk) > max_length for chunk in chunks):
        raise ValueError("A single schedule line exceeds the Telegram message limit")

    if len(chunks) > 1:
        return [
            f"{chunk}\n\nЧастина {index}/{len(chunks)}"
            for index, chunk in enumerate(chunks, start=1)
        ]
    return chunks


def send_message(text):
    chunks = split_message(text)
    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": chunk},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API rejected sendMessage: {payload}")
        log.info("Telegram message part %s/%s sent", index, len(chunks))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            value = file.read().strip()
            return value or None
    except FileNotFoundError:
        return None


def save_state(value):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        file.write(value)


def commit_state():
    subprocess.run(["git", "config", "user.name", "light-bot"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "light-bot@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(["git", "add", STATE_FILE], check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if result.returncode == 0:
        log.info("State file unchanged; no commit needed")
        return
    subprocess.run(
        ["git", "commit", "-m", "Update light schedule state"],
        check=True,
    )
    subprocess.run(["git", "push"], check=True)
    log.info("State committed and pushed")


def fetch_schedule_data():
    response = requests.get(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "all-light-bot/1.0"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    # The DTEK worker can wrap the actual API response in a JSON string in "body".
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, str):
        payload = json.loads(body)

    if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
        raise ValueError("Unexpected API response: expected an object containing regions[]")
    return payload


def find_region(payload):
    for region in payload["regions"]:
        if region.get("cpu") == REGION_ID:
            return region
    available = [region.get("cpu") for region in payload["regions"]]
    raise ValueError(
        f"Region {REGION_ID!r} was not found in API. Available region IDs: {available}"
    )


def queue_label(raw_queue):
    value = str(raw_queue).upper().replace("GPV", "").replace(" ", "")
    if value in QUEUE_LABELS:
        return QUEUE_LABELS[value]
    return value or str(raw_queue)


def normalize_status(value):
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if code == 1:
        return "on"
    if code == 2:
        return "off"
    return "unknown"


def get_slots(day_map):
    """Convert the API's HH:MM -> status map into 48 half-hour states."""
    if not isinstance(day_map, dict) or not day_map:
        return []
    slots = []
    for hour in range(24):
        for minute in (0, 30):
            key = f"{hour:02d}:{minute:02d}"
            slots.append(normalize_status(day_map.get(key)))
    return slots


def format_time(minutes):
    if minutes == 24 * 60:
        return "24:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def outage_intervals(day_map):
    slots = get_slots(day_map)
    if not slots:
        return [], False

    intervals = []
    start = None
    for index, status in enumerate(slots + ["on"]):
        if status == "off" and start is None:
            start = index * 30
        elif status != "off" and start is not None:
            intervals.append((start, index * 30))
            start = None

    return intervals, "unknown" in slots


def format_day(queue, day_label, date_value, day_map):
    intervals, has_unknown = outage_intervals(day_map)
    lines = [f"Черга {queue} — {day_label} ({date_value})"]

    if not day_map or not get_slots(day_map):
        lines.append("⚪ Немає даних")
        return lines

    if intervals:
        lines.extend(
            f"🔴 {format_time(start)}–{format_time(end)}"
            for start, end in intervals
        )
    elif not has_unknown:
        lines.append("🟢 Планових відключень не вказано")
    else:
        lines.append("⚪ Немає підтверджених інтервалів відключення")

    if has_unknown:
        lines.append("⚠️ Частина інтервалів невідома")
    return lines


def build_message(payload):
    region = find_region(payload)
    schedule = region.get("schedule") or {}
    today = payload.get("date_today")
    tomorrow = payload.get("date_tomorrow")

    if not today:
        today = datetime.now(KYIV_TZ).date().isoformat()
    if not tomorrow:
        tomorrow = (datetime.fromisoformat(today).date() + timedelta(days=1)).isoformat()

    queues = sorted(
        schedule.keys(),
        key=lambda value: (
            int("".join(c for c in queue_label(value).split(".")[0] if c.isdigit()) or 99),
            queue_label(value),
        ),
    )
    if not queues:
        raise ValueError(f"No queues found for region {REGION_ID}")

    # Do not include the current polling time here: it would make every run
    # look like a schedule change and spam the Telegram channel.
    lines = ["💡 Графік відключень — Київська область", ""]

    for raw_queue in queues:
        label = queue_label(raw_queue)
        queue_schedule = schedule.get(raw_queue) or {}
        lines.extend(format_day(label, "сьогодні", today, queue_schedule.get(today)))
        lines.append("")
        lines.extend(format_day(label, "завтра", tomorrow, queue_schedule.get(tomorrow)))
        lines.append("")

    lines.append("Джерело даних: https://svitlo.live/kiivska-oblast")
    return "\n".join(lines).strip()


def process():
    payload = fetch_schedule_data()
    message = build_message(payload)
    new_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    old_hash = load_state()

    log.info("Parsed schedules for all available queues in %s", REGION_ID)
    if old_hash == new_hash:
        log.info("No schedule changes")
        return

    # Preserve the original first-run behavior: save baseline without posting it.
    if old_hash is None:
        save_state(new_hash)
        commit_state()
        log.info("Initial schedule state saved; message not posted")
        return

    # Store the new hash only after Telegram accepts the message.
    send_message(message)
    save_state(new_hash)
    commit_state()


if __name__ == "__main__":
    process()
