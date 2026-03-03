import requests
import re
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------- CONFIG ----------------

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

QUEUE = "GPV1.2"
QUEUE_NAME = "Черга 1.2"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")

# ---------------- STATE ----------------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except:
        return None

def save_state(value):
    with open(STATE_FILE, "w") as f:
        f.write(value)

# ---------------- TELEGRAM ----------------

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })

# ---------------- HELPERS ----------------

def build_intervals(fact_data):
    intervals = []
    current = None

    for hour in range(1, 25):
        h = str(hour)
        status = fact_data.get(h)

        if status in ["no", "first", "second"]:

            start_hour = hour - 1
            end_hour = hour

            if status == "no":
                block_start = start_hour * 60
                block_end = end_hour * 60

            elif status == "first":
                block_start = start_hour * 60
                block_end = start_hour * 60 + 30

            elif status == "second":
                block_start = start_hour * 60 + 30
                block_end = end_hour * 60

            if current is None:
                current = [block_start, block_end]
            else:
                if block_start == current[1]:
                    current[1] = block_end
                else:
                    intervals.append(current)
                    current = [block_start, block_end]

        else:
            if current:
                intervals.append(current)
                current = None

    if current:
        intervals.append(current)

    return intervals


def format_time(minutes):
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

# ---------------- MAIN ----------------

def main():

    session = requests.Session()

    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})
    if r1.status_code != 200:
        return

    csrf_match = re.search(
        r'name="csrf-token" content="(.+?)"',
        r1.text
    )

    if not csrf_match:
        return

    csrf_token = csrf_match.group(1)

    now_str = datetime.now(KYIV_TZ).strftime("%H:%M %d.%m.%Y")

    payload = {
        "method": "getHomeNum",
        "data[0][name]": "city",
        "data[0][value]": CITY,
        "data[1][name]": "street",
        "data[1][value]": STREET,
        "data[2][name]": "updateFact",
        "data[2][value]": now_str
    }

    headers_post = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf_token
    }

    r2 = session.post(API_URL, data=payload, headers=headers_post)

    if r2.status_code != 200:
        return

    data = r2.json()

    today_timestamp = data["fact"]["today"]

    # Переводимо timestamp в дату Києва
    today_date = datetime.fromtimestamp(
        today_timestamp,
        tz=KYIV_TZ
    ).date()

    fact_data = data["fact"]["data"][str(today_timestamp)][QUEUE]

    intervals = build_intervals(fact_data)

    if not intervals:
        message = f"{QUEUE_NAME}\nДо кінця доби світло буде"
    else:
        message = f"{QUEUE_NAME}\nСвітла не буде:\n"
        for start, end in intervals:
            message += f"{format_time(start)}–{format_time(end)}\n"

    new_hash = hashlib.md5(message.encode()).hexdigest()
    old_hash = load_state()

    if new_hash == old_hash:
        return

    save_state(new_hash)
    send_message(message)


if __name__ == "__main__":
    main()
