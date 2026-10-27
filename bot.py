import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
GITHUB_STATE_API = "https://api.github.com/repos/stillserhio-netizen/all-light-bot/contents/state.json"
GITHUB_STATE_BRANCH = "main"
GITHUB_STATE_TOKEN = os.environ.get("GITHUB_STATE_TOKEN", "").strip()
KYIV_TZ = ZoneInfo("Europe/Kyiv")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = "-1003802691352"
QUEUE_12_CHAT_ID = "-1004430228294"
# Temporary safety switch: pause only queue 1.2 schedule posts while diagnosing source data.
QUEUE_12_SCHEDULE_PUBLISH_ENABLED = os.environ.get("QUEUE_12_SCHEDULE_PUBLISH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
HTTP_TIMEOUT = 30

QUEUE_LABELS = {
    "1.1": "1.1", "1.2": "1.2",
    "2.1": "2.1", "2.2": "2.2",
    "3.1": "3.1", "3.2": "3.2",
    "4.1": "4.1", "4.2": "4.2",
    "5.1": "5.1", "5.2": "5.2",
    "6.1": "6.1", "6.2": "6.2",
}

QUEUE_BLOCK_RE = re.compile(r"^Черга? (.+?) — (\d{2}\.\d{2}\.\d{4})(?: \((?:сьогодні|на завтра)\))?$")


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


def format_telegram_message(text):
    """Remove the date suffix from any published queue header."""
    lines = []
    for line in text.splitlines():
        if "Черга " in line or line.startswith("Черги "):
            line = re.sub(r" — \d{2}\.\d{2}\.\d{4}(?: \((?:сьогодні|на завтра)\))?", "", line)
        lines.append(line)
    return "\n".join(lines)


def send_message(text, chat_id=CHAT_ID):
    text = format_telegram_message(text)
    log.info("Sending Telegram message to chat_id=%s", chat_id)
    chunks = split_message(text)
    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": chunk},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API rejected sendMessage: {payload}")
        log.info("Telegram message part %s/%s sent", index, len(chunks))


def state_request_headers():
    if not GITHUB_STATE_TOKEN:
        return None
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_STATE_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "all-light-bot",
    }


def load_state():
    headers = state_request_headers()
    if headers:
        response = requests.get(
            GITHUB_STATE_API,
            headers=headers,
            params={"ref": GITHUB_STATE_BRANCH},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 404:
            log.info("Persistent GitHub state file does not exist yet")
            return None
        response.raise_for_status()
        payload = response.json()
        raw = base64.b64decode(payload["content"]).decode("utf-8").strip()
        if not raw:
            return None
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get("hash"), str):
            raise ValueError("GitHub state.json has an invalid format")
        return {
            "hash": state["hash"],
            "message": state.get("message") if isinstance(state.get("message"), str) else None,
            "sent_alerts": state.get("sent_alerts", []) if isinstance(state.get("sent_alerts", []), list) else [],
            "queue_12_hash": state.get("queue_12_hash") if isinstance(state.get("queue_12_hash"), str) else None,
            "queue_12_message": state.get("queue_12_message") if isinstance(state.get("queue_12_message"), str) else None,
            "queue_12_bootstrap_sent": state.get("queue_12_bootstrap_sent") is True,
            "queue_12_published_date": state.get("queue_12_published_date") if isinstance(state.get("queue_12_published_date"), str) else None,
            "queue_12_tomorrow_published_date": state.get("queue_12_tomorrow_published_date") if isinstance(state.get("queue_12_tomorrow_published_date"), str) else None,
            "emergency_status": state.get("emergency_status") if isinstance(state.get("emergency_status"), bool) else None,
            "emergency_message": state.get("emergency_message") if isinstance(state.get("emergency_message"), str) else None,
            "legacy": False,
            "backend": "github",
            "sha": payload["sha"],
        }

    log.warning("GITHUB_STATE_TOKEN is not configured; using temporary local state")
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            value = file.read().strip()
    except FileNotFoundError:
        return None

    if not value:
        return None

    try:
        state = json.loads(value)
        if isinstance(state, dict) and isinstance(state.get("hash"), str):
            return {
                "hash": state["hash"],
                "message": state.get("message") if isinstance(state.get("message"), str) else None,
                "sent_alerts": state.get("sent_alerts", []) if isinstance(state.get("sent_alerts", []), list) else [],
                "queue_12_hash": state.get("queue_12_hash") if isinstance(state.get("queue_12_hash"), str) else None,
                "queue_12_message": state.get("queue_12_message") if isinstance(state.get("queue_12_message"), str) else None,
                "queue_12_bootstrap_sent": state.get("queue_12_bootstrap_sent") is True,
            "queue_12_published_date": state.get("queue_12_published_date") if isinstance(state.get("queue_12_published_date"), str) else None,
                "emergency_status": state.get("emergency_status") if isinstance(state.get("emergency_status"), bool) else None,
                "emergency_message": state.get("emergency_message") if isinstance(state.get("emergency_message"), str) else None,
                "legacy": False,
                "backend": "local",
            }
    except json.JSONDecodeError:
        pass

    if re.fullmatch(r"[0-9a-f]{64}", value):
        return {"hash": value, "message": None, "legacy": True, "backend": "local"}

    log.warning("Local state file has an unrecognized format; treating it as missing")
    return None


def save_state(value, message, old_state=None, sent_alerts=None, queue_12_hash=None, queue_12_message=None):
    state = {
        "hash": value,
        "message": message,
        "sent_alerts": sent_alerts if sent_alerts is not None else (
            old_state.get("sent_alerts", []) if old_state else []
        ),
        "queue_12_hash": queue_12_hash if queue_12_hash is not None else (
            old_state.get("queue_12_hash") if old_state else None
        ),
        "queue_12_message": queue_12_message if queue_12_message is not None else (
            old_state.get("queue_12_message") if old_state else None
        ),
        "queue_12_bootstrap_sent": old_state.get("queue_12_bootstrap_sent", False) if old_state else False,
        "queue_12_published_date": old_state.get("queue_12_published_date") if old_state else None,
        "queue_12_tomorrow_published_date": old_state.get("queue_12_tomorrow_published_date") if old_state else None,
        "emergency_status": old_state.get("emergency_status") if old_state else None,
        "emergency_message": old_state.get("emergency_message") if old_state else None,
    }
    headers = state_request_headers()
    if headers:
        payload = {
            "message": "Update persistent schedule state",
            "branch": GITHUB_STATE_BRANCH,
            "content": base64.b64encode(
                json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii"),
        }
        if old_state and old_state.get("backend") == "github" and old_state.get("sha"):
            payload["sha"] = old_state["sha"]
        response = requests.put(
            GITHUB_STATE_API,
            headers=headers,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        log.info("Persistent schedule state saved to GitHub")
        return

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, separators=(",", ":"))
    log.warning("Schedule state saved locally only; configure GITHUB_STATE_TOKEN for persistence")


def add_update_time(message):
    now = datetime.now(KYIV_TZ)
    return f"{message}\n\n🕒 Час оновлення: {now.strftime('%d.%m.%Y о %H:%M')}"


def display_date(date_value):
    return datetime.fromisoformat(date_value).strftime("%d.%m.%Y")


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


def has_schedule_data(day_map):
    return any(status in ("on", "off") for status in get_slots(day_map))


def format_day(queue, date_value, day_map, day_label, hide_past=False):
    intervals, has_unknown = outage_intervals(day_map)
    if hide_past and date_value == datetime.now(KYIV_TZ).date().isoformat():
        now = datetime.now(KYIV_TZ)
        current_minutes = now.hour * 60 + now.minute
        intervals = [(start, end) for start, end in intervals if end > current_minutes]
    lines = [f"Черга {queue} — {display_date(date_value)} ({day_label})"]

    if not has_schedule_data(day_map):
        lines.append("⚪ Немає даних")
        return lines

    if intervals:
        lines.extend(
            f"🔴 {format_time(start)}–{format_time(end)}"
            for start, end in intervals
        )
    elif not has_unknown:
        lines.append("🟢 Без відключень")
    else:
        lines.append("⚪ Немає підтверджених інтервалів відключення")

    if has_unknown:
        lines.append("⚠️ Частина інтервалів невідома")
    return lines


def build_message(payload, only_queue=None, hide_past_today=True):
    region = find_region(payload)
    schedule = region.get("schedule") or {}
    # Use Kyiv's actual calendar date, not potentially stale API date labels.
    # Around midnight the upstream API may temporarily return the same date for today/tomorrow.
    today = datetime.now(KYIV_TZ).date().isoformat()
    tomorrow = (datetime.now(KYIV_TZ).date() + timedelta(days=1)).isoformat()

    queues = sorted(
        schedule.keys(),
        key=lambda value: (
            int("".join(c for c in queue_label(value).split(".")[0] if c.isdigit()) or 99),
            queue_label(value),
        ),
    )
    if only_queue is not None:
        queues = [queue for queue in queues if queue_label(queue) == only_queue]
    if not queues:
        raise ValueError(f"No queues found for region {REGION_ID} and filter {only_queue!r}")

    def day_lines(date_value, day_label, hide_past=False):
        result = []
        for raw_queue in queues:
            day_map = (schedule.get(raw_queue) or {}).get(date_value)
            if not has_schedule_data(day_map):
                continue

            intervals, has_unknown = outage_intervals(day_map)
            if hide_past and date_value == datetime.now(KYIV_TZ).date().isoformat():
                now = datetime.now(KYIV_TZ)
                current_minutes = now.hour * 60 + now.minute
                intervals = [(a, b) for a, b in intervals if b > current_minutes]

            result.append(
                f"Черга {queue_label(raw_queue)} — {display_date(date_value)}"
            )
            if intervals:
                result.extend(
                    f"🔴 {format_time(a)}–{format_time(b)}"
                    for a, b in intervals
                )
            elif not has_unknown:
                result.append("🟢 Без відключень")
            else:
                result.append("⚪ Немає підтверджених інтервалів відключення")

            if has_unknown:
                result.append("⚠️ Частина інтервалів невідома")
            result.append("")
        return result

    tomorrow_exists = any(
        has_schedule_data((schedule.get(queue) or {}).get(tomorrow))
        for queue in queues
    )
    lines = [
        f"📅 ГРАФІК НА СЬОГОДНІ — {display_date(today)}",
        "",
    ]
    lines.extend(day_lines(today, "сьогодні", hide_past=hide_past_today))

    if tomorrow_exists:
        lines.extend(["", f"📅 ГРАФІК НА ЗАВТРА — {display_date(tomorrow)}", ""])
        lines.extend(day_lines(tomorrow, "на завтра"))

    return "\n".join(lines).strip()


def extract_queue_blocks(message):
    """Expand grouped queue headers into individual queue/day entries for comparisons."""
    blocks = {}
    current_labels = []
    current_date = None
    current_lines = []

    def store_current():
        if current_date is None:
            return
        body = "\n".join(current_lines).strip() or "⚪ Немає даних"
        for label in current_labels:
            blocks[(label.strip(), current_date)] = body

    for line in message.splitlines():
        match = QUEUE_BLOCK_RE.match(line)
        if match:
            store_current()
            current_labels = [label.strip() for label in match.group(1).split(",")]
            current_date = match.group(2)
            current_lines = []
        elif current_date is not None:
            if not line.strip():
                store_current()
                current_labels = []
                current_date = None
                current_lines = []
            else:
                current_lines.append(line)
    store_current()
    return blocks


def normalize_elapsed_schedule_body(body, current_minutes):
    """Normalize today's schedule by excluding intervals that have already ended."""
    interval_lines = []
    has_unknown = "⚠️ Частина інтервалів невідома" in body
    no_data = "⚪ Немає даних" in body or "⚪ Даних раніше не було" in body
    for line in body.splitlines():
        match = re.search(r"🔴\s*(\d{1,2}:\d{2})–(\d{1,2}:\d{2})", line)
        if not match:
            continue
        end_hour, end_minute = map(int, match.group(2).split(":"))
        if end_hour * 60 + end_minute > current_minutes:
            interval_lines.append(line)
    if interval_lines:
        if has_unknown:
            interval_lines.append("⚠️ Частина інтервалів невідома")
        return "\n".join(interval_lines)
    if has_unknown:
        return "⚪ Немає підтверджених інтервалів відключення\n⚠️ Частина інтервалів невідома"
    if no_data:
        return "⚪ Немає даних"
    return "🟢 Без відключень"


def queue_schedule_has_real_changes(old_message, new_message, today_date):
    """Ignore elapsed intervals for today, but compare future dates in full."""
    old_blocks = extract_queue_blocks(old_message or "")
    new_blocks = extract_queue_blocks(new_message or "")
    today_display = display_date(today_date)
    now = datetime.now(KYIV_TZ)
    current_minutes = now.hour * 60 + now.minute
    # Only compare queue 1.2; the previous full-channel message contains other queues.
    keys = {key for key in set(old_blocks) | set(new_blocks) if key[0] == "1.2"}
    for key in keys:
        _, date_label = key
        old_body = old_blocks.get(key, "⚪ Даних раніше не було")
        new_body = new_blocks.get(key, "⚪ Немає даних")
        if date_label == today_display:
            old_body = normalize_elapsed_schedule_body(old_body, current_minutes)
            new_body = normalize_elapsed_schedule_body(new_body, current_minutes)
        if old_body != new_body:
            return True
    return False


def build_schedule_with_changes(old_message, new_message, today_date):
    """Mark real schedule changes, ignoring outages that have already ended."""
    old_blocks = extract_queue_blocks(old_message or "")
    today_display = display_date(today_date)
    now = datetime.now(KYIV_TZ)
    current_minutes = now.hour * 60 + now.minute

    def normalize_today_body(body):
        """Ignore elapsed outage intervals while preserving unknown-data status."""
        interval_lines = []
        has_unknown = "⚠️ Частина інтервалів невідома" in body
        no_data = "⚪ Немає даних" in body or "⚪ Даних раніше не було" in body
        for line in body.splitlines():
            match = re.search(r"🔴\s*(\d{1,2}:\d{2})–(\d{1,2}:\d{2})", line)
            if not match:
                continue
            end_hour, end_minute = map(int, match.group(2).split(":"))
            if end_hour * 60 + end_minute > current_minutes:
                interval_lines.append(line)

        if interval_lines:
            normalized = interval_lines
            if has_unknown:
                normalized.append("⚠️ Частина інтервалів невідома")
            return "\n".join(normalized)
        if has_unknown:
            return "⚪ Немає підтверджених інтервалів відключення\n⚠️ Частина інтервалів невідома"
        if no_data:
            return "⚪ Немає даних"
        return "🟢 Без відключень"
    output = []
    current_labels = []
    current_date = None
    current_day_label = ""
    current_lines = []

    def flush_block():
        if current_date is None:
            return
        header = f"Черга {', '.join(current_labels)} — {current_date}"
        body = "\n".join(current_lines).strip()
        changed_labels = []
        if current_date is not None:
            changed_labels = [
                label for label in current_labels
                if (
                    normalize_today_body(
                        old_blocks.get((label, current_date), "⚪ Даних раніше не було")
                    ) != normalize_today_body(body or "⚪ Немає даних")
                    if current_date == today_display
                    else old_blocks.get((label, current_date), "⚪ Даних раніше не було")
                    != (body or "⚪ Немає даних")
                )
            ]

        unchanged_labels = [label for label in current_labels if label not in changed_labels]

        if changed_labels:
            previous_parts = []
            for label in changed_labels:
                previous = old_blocks.get((label, current_date))
                if previous is None:
                    previous = "⚪ Даних раніше не було"
                else:
                    previous = normalize_today_body(previous)
                previous_parts.append(f"{label}: {previous.replace(chr(10), ' / ')}")
            changed_header = f"Черга {', '.join(changed_labels)} — {current_date}"
            output.append(f"🚨 {changed_header}")
            output.append(f"Було → Стало:")
            output.append(f"🔻 Було: {'; '.join(previous_parts)}")
            output.append(f"🔺 Стало: {body.replace(chr(10), ' / ')}")
            output.append("")

        # Do not lose queues grouped with a changed queue if their own schedule did not change.
        if unchanged_labels:
            unchanged_header = f"Черга {', '.join(unchanged_labels)} — {current_date}"
            output.append(unchanged_header)
            if body:
                output.extend(current_lines)
            output.append("")
        elif not changed_labels:
            output.append(header)
            if body:
                output.extend(current_lines)
            output.append("")

    for line in new_message.splitlines():
        match = QUEUE_BLOCK_RE.match(line)
        if match:
            flush_block()
            current_labels = [label.strip() for label in match.group(1).split(",")]
            current_date = match.group(2)
            current_day_label = line
            current_lines = []
        elif line.startswith("📅 ГРАФІК НА "):
            flush_block()
            current_labels = []
            current_date = None
            current_lines = []
            output.append(line)
        elif current_date is None:
            output.append(line)
        elif not line.strip():
            continue
        else:
            current_lines.append(line)

    flush_block()
    return "\n".join(output).strip()


def send_upcoming_event_alerts(payload, old_state):
    """Send alerts for transitions within the next 45 minutes, with persistent deduplication."""
    now = datetime.now(KYIV_TZ)
    sent_alerts = list(old_state.get("sent_alerts", [])) if old_state else []
    sent_set = set(sent_alerts)
    region = find_region(payload)
    schedule = region.get("schedule") or {}
    today = payload.get("date_today") or now.date().isoformat()
    tomorrow = payload.get("date_tomorrow") or (datetime.fromisoformat(today).date() + timedelta(days=1)).isoformat()
    changed = False

    for raw_queue, queue_days in schedule.items():
        queue = queue_label(raw_queue)
        for date_value in (today, tomorrow):
            day_map = (queue_days or {}).get(date_value)
            slots = get_slots(day_map)
            if not slots:
                continue
            for minute in range(0, 24 * 60, 30):
                idx = minute // 30
                status = slots[idx]
                previous = slots[idx - 1] if idx > 0 else "unknown"
                next_status = slots[idx + 1] if idx + 1 < len(slots) else "unknown"
                events = []
                if status == "off" and previous != "off":
                    events.append(("off", minute))
                if status == "on" and previous == "off":
                    events.append(("on", minute))
                for event, event_minute in events:
                    event_dt = datetime.fromisoformat(date_value).replace(tzinfo=KYIV_TZ) + timedelta(minutes=event_minute)
                    minutes_until = (event_dt - now).total_seconds() / 60
                    # Diagnostic mode: log upcoming transitions within 2 hours without sending test messages.
                    if 0 < minutes_until <= 120:
                        log.info(
                            "ALERT_DIAGNOSTIC queue=%s event=%s scheduled_at=%s minutes_until=%.1f window_match=%s",
                            queue,
                            event,
                            f"{date_value} {format_time(event_minute)}",
                            minutes_until,
                            0 < minutes_until <= 45,
                        )
                    if 0 < minutes_until <= 45:
                        key = f"{queue}|{date_value}|{event}|{event_minute}"
                        if key in sent_set:
                            continue
                        if event == "off":
                            text = f"🔦 Черга {queue} — вимкнення світла о {format_time(event_minute)}"
                        else:
                            text = f"💡 Черга {queue} — ввімкнення світла о {format_time(event_minute)}"
                        # Deliver queue 1.2 to its dedicated channel first, so a failure in the main channel cannot suppress it.
                        if queue == "1.2":
                            send_message(text, chat_id=QUEUE_12_CHAT_ID)
                            log.info("Upcoming %s alert sent to queue 1.2 channel at %s", event, format_time(event_minute))
                        send_message(text)
                        sent_set.add(key)
                        changed = True
                        log.info("Upcoming %s alert sent for queue %s at %s", event, queue, format_time(event_minute))

    # Keep state bounded to recent and upcoming days.
    cutoff = (now.date() - timedelta(days=2)).isoformat()
    sent_alerts = [key for key in sent_set if len(key.split("|")) == 4 and key.split("|")[1] >= cutoff]
    return sent_alerts, changed



class DtekNoticeParser(HTMLParser):
    """Extract visible text from likely DTEK alert/banner containers."""
    TARGET_CLASSES = {"m-attention__text", "modal-content", "popup-content", "modal-body", "tgme_widget_message_text"}
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.capture_depth = None
        self.capture_parts = []
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        is_target = bool(classes & self.TARGET_CLASSES) or attrs.get("role") == "dialog"
        if is_target and self.capture_depth is None:
            self.capture_depth = len(self.stack) + 1
            self.capture_parts = []
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.capture_depth is not None and data.strip():
            self.capture_parts.append(data.strip())

    def handle_endtag(self, tag):
        # Recover gracefully from imperfect HTML by closing through the matching tag.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                break
        if self.capture_depth is not None and len(self.stack) < self.capture_depth:
            text = " ".join(self.capture_parts)
            text = re.sub(r"\s+", " ", unescape(text)).strip()
            if text:
                self.candidates.append(text)
            self.capture_depth = None
            self.capture_parts = []


def classify_emergency_notice(text):
    """Classify only explicit current outage statements; warnings about avoiding outages are not active."""
    low = re.sub(r"\s+", " ", (text or "").lower()).strip()
    cancellation_phrases = (
        "екстрені відключення скасовуються",
        "екстрені відключення скасовано",
        "екстрені відключення скасовані",
        "скасовано екстрені відключення",
        "аварійні відключення скасовуються",
        "аварійні відключення скасовано",
        "аварійні відключення скасовані",
        "скасовано аварійні відключення",
        "аварійні відключення припинено",
        "аварійні відключення завершено",
        "повертаємось до графіків",
        "повертаємося до графіків",
    )
    if any(phrase in low for phrase in cancellation_phrases):
        return "cancelled"

    # A general appeal like "щоб уникнути екстрених/аварійних відключень" is not evidence
    # that outages are currently in force. Require explicit present-tense status.
    active_patterns = (
        r"(?:діють|застосовуються|застосовано|введені|введено|тривають|діють наразі)"
        r".{0,100}(?:екстрен\w*|аварійн\w*).{0,40}відключ",
        r"(?:екстрен\w*|аварійн\w*).{0,40}відключ\w*.{0,100}"
        r"(?:діють|застосовуються|застосовано|введені|введено|тривають)",
    )
    if any(re.search(pattern, low) for pattern in active_patterns):
        return "active"
    return "unknown"


def fetch_dtek_emergency_notice():
    """Read a current regional notice. Never infer cancellation from a missing banner."""
    urls = (
        ("website", "https://www.dtek-krem.com.ua/ua/shutdowns"),
        ("telegram", "https://t.me/s/dtek_ua"),
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; all-light-bot/1.0)",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.5",
    }
    for source, url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            parser = DtekNoticeParser()
            parser.feed(response.text)
            candidates = [
                text for text in parser.candidates
                if any(word in text.lower() for word in ("екстрен", "аварійн", "відключ"))
            ]
            if source == "telegram":
                candidates = [
                    text for text in candidates
                    if any(place in text.lower() for place in (
                        "київщина", "київська область", "київській області", "київської області",
                        "бучанському", "обухівському", "фастівському"
                    ))
                ]
            if not candidates:
                log.warning("No emergency/cancellation notice found in %s response", source)
                continue
            # The website banner is usually the most complete; Telegram is newest-first.
            notice = max(candidates, key=len) if source == "website" else candidates[0]
            status = classify_emergency_notice(notice)
            if status != "unknown":
                log.info("DTEK notice classified as %s from %s", status, source)
                return {"status": status, "text": notice[:1500], "source": source}
        except Exception as exc:
            log.warning("Could not read DTEK notice from %s: %s", source, exc)
    return None

def format_emergency_notice(notice_text):
    """Format DTEK emergency notice with affected districts clearly highlighted."""
    text = (notice_text or "").replace("\\n", "\n").replace("\\r", "")
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()
    district_names = (
        ("БУЧАНСЬКИЙ РАЙОН", ("бучанському", "бучанський район", "бучанського району")),
        ("ОБУХІВСЬКИЙ РАЙОН", ("обухівському", "обухівський район", "обухівського району")),
        ("ФАСТІВСЬКИЙ РАЙОН", ("фастівському", "фастівський район", "фастівського району")),
    )
    affected = [label for label, variants in district_names if any(v in low for v in variants)]
    if affected:
        districts = "\n".join("📍 " + label for label in affected)
        return (
            "🚨 ЕКСТРЕНІ ВІДКЛЮЧЕННЯ — КИЇВСЬКА ОБЛАСТЬ\n\n"
            "‼️ Екстрені відключення застосовуються в таких районах:\n\n"
            + districts
            + "\n\n⚠️ Під час екстрених відключень графіки не діють.\n"
            "💡 Про зміни повідомимо в цьому Telegram-каналі."
        )
    return "🚨 ЕКСТРЕНІ ВІДКЛЮЧЕННЯ — КИЇВСЬКА ОБЛАСТЬ\n\n" + text


def process_emergency_status(payload, old_state):
    """Publish only evidence-backed emergency transitions; API false alone is not cancellation."""
    region = find_region(payload)
    raw_status = region.get("emergency")
    if not isinstance(raw_status, bool):
        log.warning("API response has no boolean emergency field for %s", REGION_ID)
        return False

    previous_status = old_state.get("emergency_status")
    previous_message = old_state.get("emergency_message")
    notice_info = fetch_dtek_emergency_notice()
    notice_status = notice_info.get("status") if notice_info else None
    notice_text = notice_info.get("text") if notice_info else None

    # Positive evidence of active emergency on DTEK's notice overrides a stale API false.
    if notice_status == "active" or raw_status is True:
        if notice_status == "active" and notice_text:
            alert_text = format_emergency_notice(notice_text)
        else:
            alert_text = (
                "🚨 У Київській області діють екстрені відключення світла. "
                "Перелік районів тимчасово не вдалося підтвердити за повідомленням ДТЕК."
            )
        changed = previous_status is not True or previous_message != alert_text
        if changed:
            send_message(alert_text)
            log.info("Emergency active alert published; notice_source=%s", notice_info.get("source") if notice_info else "unavailable")
        old_state["emergency_status"] = True
        old_state["emergency_message"] = alert_text
        return changed

    # A cancellation requires an explicit cancellation notice from DTEK.
    if notice_status == "cancelled":
        changed = previous_status is True
        if changed:
            send_message(
                "✅ ДТЕК повідомив про скасування екстрених відключень. "
                "Перевірте актуальне повідомлення на сайті ДТЕК: https://www.dtek-krem.com.ua/ua/shutdowns"
            )
            log.info("Explicit DTEK emergency cancellation published")
        old_state["emergency_status"] = False
        old_state["emergency_message"] = None
        return changed

    # API false + missing/unclear DTEK notice is not sufficient evidence to announce cancellation.
    if previous_status is True:
        log.warning("API says emergency=false, but no explicit DTEK cancellation notice; preserving emergency state")
        return False

    old_state["emergency_status"] = False
    old_state["emergency_message"] = None
    return False

def process():
    payload = fetch_schedule_data()
    message = build_message(payload)
    message_12 = build_message(payload, only_queue="1.2")
    # Hash the full schedule, including elapsed intervals, so the rendered message changing as time passes does not trigger duplicate posts.
    canonical_message = build_message(payload, hide_past_today=False)
    canonical_message_12 = build_message(payload, only_queue="1.2", hide_past_today=False)
    new_hash = hashlib.sha256(("schedule-v7|publish-tomorrow-separately|" + canonical_message).encode("utf-8")).hexdigest()
    new_hash_12 = hashlib.sha256(("schedule-v7|" + canonical_message_12).encode("utf-8")).hexdigest()
    old_state = load_state()
    first_run = old_state is None
    if old_state is None:
        old_state = {
            "hash": None,
            "message": None,
            "sent_alerts": [],
            "queue_12_hash": None,
            "queue_12_message": None,
            "emergency_status": None,
            "emergency_message": None,
        }

    # Emergency alerts are independent of schedule changes and have their own persistent deduplication.
    emergency_changed = process_emergency_status(payload, old_state)

    log.info("Parsed schedules for all available queues in %s", REGION_ID)
    # Log raw queue 1.2 source data (today/tomorrow) for diagnosis.
    raw_schedule = find_region(payload).get("schedule") or {}
    today_diag = payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()
    tomorrow_diag = payload.get("date_tomorrow") or (datetime.fromisoformat(today_diag).date() + timedelta(days=1)).isoformat()
    for raw_queue, queue_days in raw_schedule.items():
        if queue_label(raw_queue) == "1.2":
            for diag_date in (today_diag, tomorrow_diag):
                log.info("RAW_QUEUE_SCHEDULE queue=1.2 date=%s data=%s", diag_date, json.dumps((queue_days or {}).get(diag_date), ensure_ascii=False, sort_keys=True))
    log.info("QUEUE_12_SCHEDULE_PUBLISH_ENABLED=%s", QUEUE_12_SCHEDULE_PUBLISH_ENABLED)

    # Upcoming event alerts are independent of whether the overall schedule changed.
    sent_alerts, _ = send_upcoming_event_alerts(payload, old_state)
    if first_run:
        # Keep the existing no-initial-post behavior in the main channel, but seed the new channel.
        tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
        if QUEUE_12_SCHEDULE_PUBLISH_ENABLED:
            if tomorrow_heading in message_12:
                today_part_12, tomorrow_part_12 = message_12.split(tomorrow_heading, 1)
                if today_part_12.strip():
                    send_message(add_update_time(today_part_12.strip()), chat_id=QUEUE_12_CHAT_ID)
                tomorrow_part_12 = (tomorrow_heading + tomorrow_part_12).strip()
                if tomorrow_part_12:
                    send_message(add_update_time(tomorrow_part_12), chat_id=QUEUE_12_CHAT_ID)
            else:
                send_message(add_update_time(message_12), chat_id=QUEUE_12_CHAT_ID)
        else:
            log.warning("Queue 1.2 schedule publication paused; diagnostic data logged")
        if QUEUE_12_SCHEDULE_PUBLISH_ENABLED:
            old_state["queue_12_bootstrap_sent"] = True
            old_state["queue_12_published_date"] = datetime.now(KYIV_TZ).date().isoformat()
            tomorrow_date_for_state = payload.get("date_tomorrow")
            if tomorrow_date_for_state:
                old_state["queue_12_tomorrow_published_date"] = tomorrow_date_for_state
        save_state(new_hash, message, old_state, sent_alerts, new_hash_12, message_12)
        log.info("Initial schedule baseline saved; queue 1.2 schedule published")
        return

    # Publish queue 1.2 whenever its actual addressable schedule blocks change.
    # Compare the queue-specific message against queue 1.2 blocks extracted from the
    # previous full-channel message; do not infer changes from the global hash.
    schedule_today = payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()
    old_hash_12 = old_state.get("queue_12_hash")
    # Older state files may not contain queue_12_message yet. Fall back to the
    # last full schedule message, then compare only queue 1.2 blocks.
    previous_queue_message = old_state.get("queue_12_message") or old_state.get("message") or ""
    queue_12_message_sent = False
    # One-time recovery publication: after enabling the dedicated channel, send its
    # current schedule once even if the stored hash says "unchanged".
    today_publish_date = datetime.now(KYIV_TZ).date().isoformat()
    needs_queue_12_bootstrap = (
        not old_state.get("queue_12_bootstrap_sent", False)
        or old_state.get("queue_12_published_date") != today_publish_date
    )
    if QUEUE_12_SCHEDULE_PUBLISH_ENABLED and needs_queue_12_bootstrap:
        tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
        if tomorrow_heading in message_12:
            today_part_12, tomorrow_part_12 = message_12.split(tomorrow_heading, 1)
            if today_part_12.strip():
                send_message(add_update_time(today_part_12.strip()), chat_id=QUEUE_12_CHAT_ID)
            tomorrow_part_12 = (tomorrow_heading + tomorrow_part_12).strip()
            if tomorrow_part_12:
                send_message(add_update_time(tomorrow_part_12), chat_id=QUEUE_12_CHAT_ID)
        else:
            send_message(add_update_time(message_12), chat_id=QUEUE_12_CHAT_ID)
        old_state["queue_12_bootstrap_sent"] = True
        old_state["queue_12_published_date"] = today_publish_date
        tomorrow_date_for_state = payload.get("date_tomorrow")
        if tomorrow_date_for_state:
            old_state["queue_12_tomorrow_published_date"] = tomorrow_date_for_state
        queue_12_message_sent = True
        log.info("Queue 1.2 recovery/current-day schedule published (published_date=%s)", today_publish_date)
    elif QUEUE_12_SCHEDULE_PUBLISH_ENABLED and (old_hash_12 != new_hash_12 or previous_queue_message != message_12):
        old_blocks_12 = extract_queue_blocks(previous_queue_message)
        new_blocks_12 = extract_queue_blocks(message_12)
        old_queue_blocks = {date: body for (queue, date), body in old_blocks_12.items() if queue == "1.2"}
        new_queue_blocks = {date: body for (queue, date), body in new_blocks_12.items() if queue == "1.2"}
        # A hash can differ because an outage interval ended and disappeared from
        # today's rendered message. Publish only if remaining intervals or future dates changed.
        actual_change_12 = queue_schedule_has_real_changes(
            previous_queue_message, message_12, schedule_today
        )

        # If the queue-specific rendered schedule/hash changed, publish to the
        # dedicated channel. Do not let the "real changes" filter suppress a genuine
        # queue 1.2 update that the main channel already considers publishable.
        if actual_change_12 or old_hash_12 != new_hash_12 or previous_queue_message != message_12:
            # Keep queue 1.2's existing schedule flow, but mark today's real interval changes
            # using the same 🚨 Было -> Стало format as the main channel.
            previous_message = previous_queue_message
            changed_message_12 = build_schedule_with_changes(previous_message, message_12, schedule_today)
            tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
            if tomorrow_heading in changed_message_12:
                today_part_12, tomorrow_part_12 = changed_message_12.split(tomorrow_heading, 1)
                today_part_12 = today_part_12.strip()
                tomorrow_part_12 = (tomorrow_heading + tomorrow_part_12).strip()
                if today_part_12:
                    send_message(add_update_time(today_part_12), chat_id=QUEUE_12_CHAT_ID)
                if tomorrow_part_12:
                    send_message(add_update_time(tomorrow_part_12), chat_id=QUEUE_12_CHAT_ID)
            else:
                send_message(add_update_time(changed_message_12), chat_id=QUEUE_12_CHAT_ID)
            tomorrow_date_for_state = payload.get("date_tomorrow")
            if tomorrow_date_for_state:
                old_state["queue_12_tomorrow_published_date"] = tomorrow_date_for_state
            queue_12_message_sent = True
            log.info("Queue 1.2 schedule changed; published change-marked update (old blocks=%s, new blocks=%s)",
                     old_queue_blocks, new_queue_blocks)
        else:
            log.info("Queue 1.2 schedule unchanged; duplicate post suppressed")
    # Publish the tomorrow section to the dedicated queue 1.2 channel once per target date,
    # even when tomorrow has no outages and the schedule hash is unchanged.
    tomorrow_publish_date = (datetime.now(KYIV_TZ).date() + timedelta(days=1)).isoformat()
    if QUEUE_12_SCHEDULE_PUBLISH_ENABLED and tomorrow_publish_date and old_state.get("queue_12_tomorrow_published_date") != tomorrow_publish_date:
        tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
        if tomorrow_heading in message_12:
            _, tomorrow_part_12 = message_12.split(tomorrow_heading, 1)
            tomorrow_part_12 = (tomorrow_heading + tomorrow_part_12).strip()
            if tomorrow_part_12:
                send_message(add_update_time(tomorrow_part_12), chat_id=QUEUE_12_CHAT_ID)
                old_state["queue_12_tomorrow_published_date"] = tomorrow_publish_date
                queue_12_message_sent = True
                log.info("Queue 1.2 tomorrow schedule published (date=%s), including no-outage schedules", tomorrow_publish_date)
        else:
            log.warning("Queue 1.2 tomorrow section not found in rendered message (date=%s)", tomorrow_publish_date)

    # Main-channel schedule handling remains independent of the queue 1.2 channel.
    if old_state["hash"] == new_hash:
        if (old_state.get("message") != message or old_state.get("legacy")
                or sent_alerts != old_state.get("sent_alerts", [])
                or old_hash_12 != new_hash_12 or emergency_changed or queue_12_message_sent):
            save_state(new_hash, message, old_state, sent_alerts, new_hash_12,
                       message_12 if queue_12_message_sent else None)
        log.info("Schedule unchanged in main channel; no main schedule update sent")
        return

    old_message = old_state.get("message")
    schedule_today = payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()
    schedule_with_changes = build_schedule_with_changes(old_message, message, schedule_today)
    tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
    if tomorrow_heading in schedule_with_changes:
        today_part, tomorrow_part = schedule_with_changes.split(tomorrow_heading, 1)
        today_part = today_part.strip()
        tomorrow_part = (tomorrow_heading + tomorrow_part).strip()
        if today_part:
            send_message(add_update_time(today_part))
        if tomorrow_part:
            send_message(add_update_time(tomorrow_part))
    else:
        send_message(add_update_time(schedule_with_changes))

    # Save only after all required schedule posts were sent successfully.
    save_state(new_hash, message, old_state, sent_alerts, new_hash_12,
               message_12 if queue_12_message_sent else None)
    log.info("Main schedule update published; state saved")


class HealthHandler(BaseHTTPRequestHandler):
    def _respond_health(self, include_body):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return

        body = b"all-light-bot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._respond_health(include_body=True)

    def do_HEAD(self):
        self._respond_health(include_body=False)

    def log_message(self, format_string, *args):
        log.info("HTTP %s - %s", self.address_string(), format_string % args)


def seconds_until_next_run(now):
    """Run exactly 15 minutes after each daytime check; hourly overnight, Kyiv time."""
    if 6 <= now.hour <= 23:
        return 15 * 60
    return 60 * 60


def run_scheduler():
    while True:
        try:
            process()
        except Exception:
            log.exception("Schedule check failed; keeping service alive for the next attempt")

        now = datetime.now(KYIV_TZ)
        delay = seconds_until_next_run(now)
        log.info("Next schedule check in %.0f seconds", delay)
        time.sleep(delay)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, name="health-server", daemon=True).start()
    log.info("Health endpoint listening on port %s", port)
    run_scheduler()
