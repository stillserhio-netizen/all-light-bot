import requests
import re
import hashlib
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"
KYIV_TZ = ZoneInfo("Europe/Kyiv")

ADDRESSES = [
    {
        "city": "м. Богуслав",
        "street": "вул. Теліги Олени",
        "queue_code": "GPV1.2",
        "queue_name": "Черга 1.2"
    },
    {
        "city": "м. Біла Церква",
        "street": "вул. Голуба Професора",
        "queue_code": "GPV2.2",
        "queue_name": "Черга 2.2"
    }
]

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except:
        return None

def save_state(value):
    with open(STATE_FILE, "w") as f:
        f.write(value)

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})

def format_time(minutes):
    return f"{minutes//60:02d}:{minutes%60:02d}"

def build_intervals(fact_data):
    intervals = []
    current = None

    for hour in range(1, 25):
        status = fact_data.get(str(hour))

        if status in ["no", "first", "second"]:
            start = (hour - 1) * 60
            end = hour * 60

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

    return intervals

def main():

    now = datetime.now(KYIV_TZ)
    current_minutes = now.hour * 60 + now.minute

    session = requests.Session()

    # ОДИН GET
    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})
    if r1.status_code != 200:
        return

    csrf_match = re.search(r'name="csrf-token" content="(.+?)"', r1.text)
    if not csrf_match:
        return

    csrf_token = csrf_match.group(1)

    headers_post = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf_token
    }

    message_blocks = []

    for address in ADDRESSES:

        payload = {
            "method": "getHomeNum",
            "data[0][name]": "city",
            "data[0][value]": address["city"],
            "data[1][name]": "street",
            "data[1][value]": address["street"],
            "data[2][name]": "updateFact",
            "data[2][value]": now.strftime("%H:%M %d.%m.%Y")
        }

        r2 = session.post(API_URL, data=payload, headers=headers_post)

        if r2.status_code != 200:
            message_blocks.append(f"{address['queue_name']}\nПомилка запиту")
            continue

        data = r2.json()

        if "fact" not in data:
            message_blocks.append(f"{address['queue_name']}\nАдресу не знайдено")
            continue

        all_days = data["fact"]["data"]
        timestamps = sorted(all_days.keys(), key=int)

        today_ts = timestamps[0]
        tomorrow_ts = timestamps[1] if len(timestamps) > 1 else None

        block = f"{address['queue_name']}\n"

        # ---- СЬОГОДНІ ----
        today_intervals = build_intervals(
            all_days[today_ts][address["queue_code"]]
        )

        future_today = [
            (s, e) for s, e in today_intervals if e > current_minutes
        ]

        block += "Сьогодні:\n"

        if future_today:
            for s, e in future_today:
                block += f"{format_time(s)}–{format_time(e)}\n"
        else:
            block += "До кінця доби світло буде\n"

        # ---- ЗАВТРА ----
        if tomorrow_ts:
            tomorrow_intervals = build_intervals(
                all_days[tomorrow_ts][address["queue_code"]]
            )

            block += "\nЗавтра:\n"

            if tomorrow_intervals:
                for s, e in tomorrow_intervals:
                    block += f"{format_time(s)}–{format_time(e)}\n"
            else:
                block += "До кінця доби світло буде\n"

        message_blocks.append(block.strip())

        time.sleep(2)  # невелика пауза між адресами

    final_message = "\n\n".join(message_blocks)

    new_hash = hashlib.md5(final_message.encode()).hexdigest()
    old_hash = load_state()

    if new_hash != old_hash:
        save_state(new_hash)
        send_message(final_message)

if __name__ == "__main__":
    main()
