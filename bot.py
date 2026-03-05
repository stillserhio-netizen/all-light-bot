import requests
import re
import hashlib
import time
import os
import subprocess
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


def send_message(text):

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )

    print("TELEGRAM STATUS:", r.status_code)
    print("TELEGRAM RESPONSE:", r.text)


def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE, "r") as f:
        return f.read().strip()


def save_state(value):

    with open(STATE_FILE, "w") as f:
        f.write(value)


def commit_state():

    subprocess.run(["git", "config", "--global", "user.name", "bot"])
    subprocess.run(["git", "config", "--global", "user.email", "bot@github"])

    subprocess.run(["git", "add", STATE_FILE])
    subprocess.run(["git", "commit", "-m", "update state"], check=False)
    subprocess.run(["git", "push"], check=False)


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


def get_csrf(html):

    csrf_match = re.search(r'csrf-token" content="([^"]+)"', html)

    if not csrf_match:
        csrf_match = re.search(r'content="([^"]+)" name="csrf-token"', html)

    if not csrf_match:
        return None

    return csrf_match.group(1)


def main():

    print("BOT START")

    session = requests.Session()

    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})

    print("GET STATUS:", r1.status_code)

    if r1.status_code != 200:
        return

    csrf_token = get_csrf(r1.text)

    if not csrf_token:
        print("CSRF TOKEN NOT FOUND")
        return

    print("CSRF TOKEN FOUND")

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

    for address in ADDRESSES:

        print("CHECK ADDRESS:", address["queue_name"])

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

        print("POST STATUS:", r2.status_code)

        if r2.status_code != 200:
            continue

        data = r2.json()

        if "fact" not in data:
            print("FACT NOT FOUND")
            continue

        all_days = data["fact"]["data"]

        timestamps = sorted(all_days.keys(), key=int)

        today_ts = timestamps[0]
        tomorrow_ts = timestamps[1] if len(timestamps) > 1 else None

        block = f"{address['queue_name']}\n"

        fact_today = all_days[today_ts][address["queue_code"]]

        intervals_today = build_intervals(fact_today)

        future = [(s, e) for s, e in intervals_today if e > now_minutes]

        block += "Сьогодні:\n"

        if future:
            for s, e in future:
                block += f"{format_time(s)}–{format_time(e)}\n"
        else:
            block += "До кінця доби світло буде\n"

        if tomorrow_ts:

            fact_tomorrow = all_days[tomorrow_ts][address["queue_code"]]

            intervals_tomorrow = build_intervals(fact_tomorrow)

            if intervals_tomorrow:

                block += "\nЗавтра:\n"

                for s, e in intervals_tomorrow:
                    block += f"{format_time(s)}–{format_time(e)}\n"

        message_blocks.append(block.strip())

        time.sleep(2)

    final_message = "\n\n".join(message_blocks)

    print("FINAL MESSAGE:")
    print(final_message)

    new_hash = hashlib.md5(final_message.encode()).hexdigest()
    old_hash = load_state()

    print("OLD HASH:", old_hash)
    print("NEW HASH:", new_hash)

    if old_hash is None or new_hash != old_hash:

        print("SENDING MESSAGE")

        send_message(f"📊 Оновлено графік\n\n{final_message}")

        save_state(new_hash)

        commit_state()

    else:

        print("NO CHANGES")


if __name__ == "__main__":
    main()
