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

QUEUE_BLOCK_RE = re.compile(r"^Черга ([\w.]+) — (\d{2}\.\d{2}\.\d{4})$")


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


def save_state(value, message, old_state=None):
    state = {"hash": value, "message": message}
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


def save_state(value, message):
    state = {"hash": value, "message": message}
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, separators=(",", ":"))


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


def format_day(queue, date_value, day_map):
    intervals, has_unknown = outage_intervals(day_map)
    lines = [f"Черга {queue} — {display_date(date_value)}"]

    if not has_schedule_data(day_map):
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
    if not queues:
        raise ValueError(f"No queues found for region {REGION_ID}")

    tomorrow_exists = any(
        has_schedule_data((schedule.get(queue) or {}).get(tomorrow))
        for queue in queues
    )
    lines = ["💡 Графік відключень — Київська область", ""]

    for raw_queue in queues:
        label = queue_label(raw_queue)
        queue_schedule = schedule.get(raw_queue) or {}
        today_map = queue_schedule.get(today)
        if has_schedule_data(today_map):
            lines.extend(format_day(label, today, today_map))
            lines.append("")
        if tomorrow_exists:
            tomorrow_map = queue_schedule.get(tomorrow)
            if has_schedule_data(tomorrow_map):
                lines.extend(format_day(label, tomorrow, tomorrow_map))
                lines.append("")

    return "\n".join(lines).strip()


def extract_queue_blocks(message):
    blocks = {}
    current_key = None
    current_lines = []

    def store_current():
        if current_key is not None:
            blocks[current_key] = "\n".join(current_lines).strip() or "⚪ Немає даних"

    for line in message.splitlines():
        match = QUEUE_BLOCK_RE.match(line)
        if match:
            store_current()
            current_key = match.groups()
            current_lines = []
        elif current_key is not None:
            if not line.strip():
                store_current()
                current_key = None
                current_lines = []
            else:
                current_lines.append(line)
    store_current()
    return blocks


def build_change_message(old_message, new_message):
    old_blocks = extract_queue_blocks(old_message)
    new_blocks = extract_queue_blocks(new_message)
    changes = []

    for key in sorted(set(old_blocks) | set(new_blocks)):
        old_body = old_blocks.get(key)
        new_body = new_blocks.get(key)
        if old_body == new_body:
            continue
        queue, date_value = key
        old_text = old_body if old_body is not None else "⚪ Даних раніше не було"
        new_text = new_body if new_body is not None else "⚪ Цього графіка більше немає в нових даних"
        changes.extend([
            f"Черга {queue} — {date_value}",
            "Було:",
            *[f"  {line}" for line in old_text.splitlines()],
            "Стало:",
            *[f"  {line}" for line in new_text.splitlines()],
            "",
        ])

    if not changes:
        return "🔔 Оновлення графіка — Київська область\nЗмінився лише набір дат у джерелі; інтервали відключень не змінилися."
    return "🔔 Зміни графіка — Київська область\n\n" + "\n".join(changes).strip()


def process():
    payload = fetch_schedule_data()
    message = build_message(payload)
    new_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    old_state = load_state()

    log.info("Parsed schedules for all available queues in %s", REGION_ID)

    # First successful run only establishes the baseline; do not publish.
    if old_state is None:
        save_state(new_hash, message, old_state)
        log.info("Initial schedule baseline saved; nothing published")
        return

    # No change: update stored text if needed, but do not send to Telegram.
    if old_state["hash"] == new_hash:
        if old_state.get("message") != message or old_state.get("legacy"):
            save_state(new_hash, message, old_state)
        log.info("Schedule unchanged; no Telegram messages sent")
        return

    # On a change, send the comparison first, then the full current schedule.
    old_message = old_state.get("message")
    if old_message:
        change_message = build_change_message(old_message, message)
    else:
        change_message = (
            "🔔 Зміни графіка — Київська область\n"
            "Попередній текст графіка недоступний для порівняння."
        )

    send_message(add_update_time(change_message))
    send_message(add_update_time(message))

    # Save only after both messages were sent successfully.
    save_state(new_hash, message, old_state)
    log.info("Schedule changed; change notice and full schedule sent; state saved")


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
    """Align checks to quarter-hours from 06:00–23:59 Kyiv time and hourly overnight."""
    if 6 <= now.hour <= 23:
        minute = ((now.minute // 15) + 1) * 15
        if minute >= 60:
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_run = now.replace(minute=minute, second=0, microsecond=0)
    else:
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(1, (next_run - now).total_seconds())


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
