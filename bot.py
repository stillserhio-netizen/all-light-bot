import requests
import re
import hashlib
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo


BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"
POWER_STATE_FILE = "power_state.txt"

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


def send_message(text):

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )


def load_state(path):

    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        return f.read().strip()


def save_state(path, value):

    with open(path, "w") as f:
        f.write(value)


def format_time(minutes):

    h = minutes // 60
    m = minutes % 60

    return f"{h:02d}:{m:02d}"


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


def is_power_off(now_minutes, intervals):

    for s, e in intervals:

        if s <= now_minutes < e:
            return True, s, e

    return False, None, None


def check_schedule():

    session = requests.Session()

    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})

    if r1.status_code != 200:
        return None, None, None

    csrf_match = re.search(r'name="csrf-token" content="(.+?)"', r1.text)

    if not csrf_match:
        return None, None, None

    csrf_token = csrf_match.group(1)

    headers_post = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf_token
    }

    now = datetime.now(KYIV_TZ)

    now_minutes = now.hour * 60 + now.minute

    message_blocks = []
    power_states = []

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
            continue

        data = r2.json()

        if "fact" not in data:
            continue

        all_days = data["fact"]["data"]

        timestamps = sorted(all_days.keys(), key=int)

        today_ts = timestamps[0]

        fact = all_days[today_ts][address["queue_code"]]

        intervals = build_intervals(fact)

        future = [(s, e) for s, e in intervals if e > now_minutes]

        block = f"{address['queue_name']}\n"

        if future:
            for s, e in future:
                block += f"{format_time(s)}–{format_time(e)}\n"
        else:
            block += "До кінця доби світло буде\n"

        message_blocks.append(block.strip())

        off, s, e = is_power_off(now_minutes, intervals)

        if off:
            power_states.append(
                f"🔴 {address['queue_name']}\nПочалося відключення\n{format_time(s)}–{format_time(e)}"
            )
        else:
            power_states.append(
                f"🟢 {address['queue_name']}\nСвітло є"
            )

        time.sleep(2)

    return "\n\n".join(message_blocks), "\n\n".join(power_states)


def main():

    schedule_text, power_state = check_schedule()

    if schedule_text is None:
        return

    schedule_hash = hashlib.md5(schedule_text.encode()).hexdigest()

    power_hash = hashlib.md5(power_state.encode()).hexdigest()

    old_schedule = load_state(STATE_FILE)
    old_power = load_state(POWER_STATE_FILE)

    if old_schedule is None:

        send_message(schedule_text)

        save_state(STATE_FILE, schedule_hash)

    elif schedule_hash != old_schedule:

        send_message("📊 Оновлено графік\n\n" + schedule_text)

        save_state(STATE_FILE, schedule_hash)

    if old_power is None:

        save_state(POWER_STATE_FILE, power_hash)

    elif power_hash != old_power:

        send_message(power_state)

        save_state(POWER_STATE_FILE, power_hash)


if __name__ == "__main__":
    main()
