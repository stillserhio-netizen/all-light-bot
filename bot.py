import requests
import hashlib
import os

# =========================
# ДАНІ БОТА
# =========================

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

BASE_URL = "https://www.dtek-krem.com.ua"
AJAX_URL = "https://www.dtek-krem.com.ua/ua/ajax"

STATE_FILE = "state.txt"

QUEUES = [
    "GPV1.1", "GPV1.2",
    "GPV2.1", "GPV2.2",
    "GPV3.1", "GPV3.2",
    "GPV4.1", "GPV4.2",
    "GPV5.1", "GPV5.2",
    "GPV6.1", "GPV6.2"
]

# =========================

def get_csrf(session):
    r = session.get(BASE_URL + "/ua/shutdowns")
    if r.status_code != 200:
        print("GET ERROR:", r.status_code)
        return None

    try:
        return r.text.split('name="csrf-token" content="')[1].split('"')[0]
    except:
        print("CSRF not found")
        return None


def get_schedule():
    session = requests.Session()

    csrf = get_csrf(session)
    if not csrf:
        return None

    headers = {
        "x-csrf-token": csrf,
        "x-requested-with": "XMLHttpRequest",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": BASE_URL,
        "referer": BASE_URL + "/ua/shutdowns",
        "user-agent": "Mozilla/5.0"
    }

    payload = {
        "method": "getSchedule",
        "data[0][name]": "city",
        "data[0][value]": "м. Богуслав"
    }

    r = session.post(AJAX_URL, data=payload, headers=headers)

    if r.status_code != 200:
        print("POST ERROR:", r.status_code)
        return None

    try:
        return r.json()
    except:
        print("JSON parse error")
        return None


def build_intervals(data, queue_key):

    try:
        today_key = str(data["fact"]["today"])
        day_schedule = data["fact"]["data"][today_key][queue_key]
        time_map = data["preset"]["time_zone"]
    except KeyError:
        print("JSON structure changed")
        return []

    intervals = []
    current_start = None
    current_end = None

    for hour in range(1, 25):

        status = day_schedule[str(hour)]

        if status in ["no", "first", "second"]:
            if current_start is None:
                current_start = time_map[str(hour)][1]
            current_end = time_map[str(hour)][2]
        else:
            if current_start:
                intervals.append(f"{current_start}–{current_end}")
                current_start = None

    if current_start:
        intervals.append(f"{current_start}–{current_end}")

    return intervals


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    return open(STATE_FILE).read()


def save_state(value):
    with open(STATE_FILE, "w") as f:
        f.write(value)


def main():

    data = get_schedule()
    if not data:
        print("No data received")
        return

    message = ""

    for queue in QUEUES:

        intervals = build_intervals(data, queue)

        queue_name = data.get("preset", {}).get("sch_names", {}).get(queue, queue)

        message += f"{queue_name}\n"

        if intervals:
            for interval in intervals:
                message += f"🔴 {interval}\n"
        else:
            message += "🟢 Світло буде до кінця доби\n"

        message += "\n"

    message = message.strip()

    new_hash = hashlib.md5(message.encode()).hexdigest()
    old_hash = load_state()

    print("Old:", old_hash)
    print("New:", new_hash)

    if new_hash != old_hash:
        send_message(message)
        save_state(new_hash)
        print("SENT")
    else:
        print("NO CHANGE")


if __name__ == "__main__":
    main()
