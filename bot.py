import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
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
HTTP_TIMEOUT = 30

QUEUE_LABELS = {
    "1.1": "1.1", "1.2": "1.2",
    "2.1": "2.1", "2.2": "2.2",
    "3.1": "3.1", "3.2": "3.2",
    "4.1": "4.1", "4.2": "4.2",
    "5.1": "5.1", "5.2": "5.2",
    "6.1": "6.1", "6.2": "6.2",
}

QUEUE_BLOCK_RE = re.compile(r"^Черги? (.+?) — (\d{2}\.\d{2}\.\d{4})(?: \((?:сьогодні|на завтра)\))?$")


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
                "legacy": False,
                "backend": "local",
            }
    except json.JSONDecodeError:
        pass

    if re.fullmatch(r"[0-9a-f]{64}", value):
        return {"hash": value, "message": None, "legacy": True, "backend": "local"}

    log.warning("Local state file has an unrecognized format; treating it as missing")
    return None


def save_state(value, message, old_state=None, sent_alerts=None, queue_12_hash=None):
    state = {
        "hash": value,
        "message": message,
        "sent_alerts": sent_alerts if sent_alerts is not None else (
            old_state.get("sent_alerts", []) if old_state else []
        ),
        "queue_12_hash": queue_12_hash if queue_12_hash is not None else (
            old_state.get("queue_12_hash") if old_state else None
        ),
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
    today = payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()
    tomorrow = payload.get("date_tomorrow") or (
        datetime.fromisoformat(today).date() + timedelta(days=1)
    ).isoformat()

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
        if current_date == today_display:
            changed_labels = [
                label for label in current_labels
                if normalize_today_body(
                    old_blocks.get((label, current_date), "⚪ Даних раніше не було")
                ) != normalize_today_body(body or "⚪ Немає даних")
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
            output.append(f"Було: {'; '.join(previous_parts)}")
            output.append(f"Стало: {body.replace(chr(10), ' / ')}")
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

    log.info("Parsed schedules for all available queues in %s", REGION_ID)

    # Upcoming event alerts are independent of whether the overall schedule changed.
    sent_alerts, _ = send_upcoming_event_alerts(payload, old_state)
    if old_state is None:
        # Keep the existing no-initial-post behavior in the main channel, but seed the new channel.
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
        save_state(new_hash, message, old_state, sent_alerts, new_hash_12)
        log.info("Initial schedule baseline saved; queue 1.2 schedule published")
        return

    # Publish the queue 1.2 baseline once, then only when that queue's schedule changes.
    old_hash_12 = old_state.get("queue_12_hash")
    if old_hash_12 != new_hash_12:
        if old_hash_12 is None:
            schedule_with_changes_12 = message_12
        else:
            schedule_with_changes_12 = build_schedule_with_changes(
                old_state.get("message"), message_12,
                payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()
            )
        # A changed version/hash alone must not republish the same schedule.
        # Today's real changes are marked with 🚨 by build_schedule_with_changes;
        # compare tomorrow's queue 1.2 block separately.
        actual_change_12 = "🚨" in schedule_with_changes_12
        old_blocks_12 = extract_queue_blocks(old_state.get("message") or "")
        new_blocks_12 = extract_queue_blocks(message_12)
        tomorrow_date = payload.get("date_tomorrow") or (
            datetime.fromisoformat(payload.get("date_today") or datetime.now(KYIV_TZ).date().isoformat()).date()
            + timedelta(days=1)
        ).isoformat()
        tomorrow_display = display_date(tomorrow_date)
        old_tomorrow_12 = old_blocks_12.get(("1.2", tomorrow_display))
        new_tomorrow_12 = new_blocks_12.get(("1.2", tomorrow_display))
        if old_tomorrow_12 != new_tomorrow_12:
            actual_change_12 = True

        if actual_change_12:
            tomorrow_heading = "📅 ГРАФІК НА ЗАВТРА"
            if tomorrow_heading in schedule_with_changes_12:
                today_part_12, tomorrow_part_12 = schedule_with_changes_12.split(tomorrow_heading, 1)
                today_part_12 = today_part_12.strip()
                tomorrow_part_12 = (tomorrow_heading + tomorrow_part_12).strip()
                if today_part_12 and "🚨" in today_part_12:
                    send_message(add_update_time(today_part_12), chat_id=QUEUE_12_CHAT_ID)
                if tomorrow_part_12 and old_tomorrow_12 != new_tomorrow_12:
                    send_message(add_update_time(tomorrow_part_12), chat_id=QUEUE_12_CHAT_ID)
            elif schedule_with_changes_12.strip() and "🚨" in schedule_with_changes_12:
                send_message(add_update_time(schedule_with_changes_12), chat_id=QUEUE_12_CHAT_ID)
            log.info("Queue 1.2 schedule change published to its dedicated channel")
        else:
            log.info("Queue 1.2 schedule unchanged; duplicate post suppressed")

    # Main-channel schedule handling remains independent of the queue 1.2 channel.
    if old_state["hash"] == new_hash:
        if (old_state.get("message") != message or old_state.get("legacy")
                or sent_alerts != old_state.get("sent_alerts", [])
                or old_hash_12 != new_hash_12):
            save_state(new_hash, message, old_state, sent_alerts, new_hash_12)
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
    save_state(new_hash, message, old_state, sent_alerts, new_hash_12)
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
