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

# ---------------- MAIN ----------------

def main():

    session = requests.Session()

    # GET сторінки
    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})
    if r1.status_code != 200:
        print("GET FAILED")
        return

    # CSRF
    csrf_match = re.search(
        r'name="csrf-token" content="(.+?)"',
        r1.text
    )

    if not csrf_match:
        print("NO CSRF")
        return

    csrf_token = csrf_match.group(1)

    now_str = datetime.now(
        ZoneInfo("Europe/Kyiv")
    ).strftime("%H:%M %d.%m.%Y")

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
        print("POST FAILED")
        return

    data = r2.json()

    today_key = str(data["fact"]["today"])
    fact_data = data["fact"]["data"][today_key][QUEUE]

    current_hour = str(datetime.now().hour + 1)

    status = fact_data.get(current_hour)

    # формуємо хеш
    state_string = f"{QUEUE}-{current_hour}-{status}"
    new_hash = hashlib.md5(state_string.encode()).hexdigest()

    old_hash = load_state()

    if new_hash == old_hash:
        print("NO CHANGE")
        return

    save_state(new_hash)

    time_label = (
        data["preset"]["time_zone"][current_hour][1]
        + " - " +
        data["preset"]["time_zone"][current_hour][2]
    )

    status_text = data["preset"]["time_type"].get(status, status)

    message = (
        f"{QUEUE_NAME}\n"
        f"{time_label}\n"
        f"{status_text}"
    )

    send_message(message)
    print("SENT:", message)


if __name__ == "__main__":
    main()
