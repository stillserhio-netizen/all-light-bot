import requests
import re
import hashlib
import time
import os
import logging

from datetime import datetime
from zoneinfo import ZoneInfo


# ── Logging ─────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger(__name__)


# ── Config ─────────────────────────────────

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"
REMINDER_FILE = "reminders.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


# ── ADDRESSES ───────────────────────────────

ADDRESSES = [

# {"city":"с. Карапиші","street":"вул. Молодіжна 12","queue_code":"GPV1.1","queue_name":"1.1"},

{"city":"м. Богуслав","street":"вул. Теліги Олени","queue_code":"GPV1.2","queue_name":"1.2"},

{"city":"м. Біла Церква","street":"вул. Гончара Олеся 2","queue_code":"GPV2.1","queue_name":"2.1"},

# {"city":"м. Біла Церква","street":"вул. Голуба Професора","queue_code":"GPV2.2","queue_name":"2.2"},
# {"city":"м. Миронівка","street":"вул. Шевченка 2","queue_code":"GPV3.1","queue_name":"3.1"},
# {"city":"м. Миронівка","street":"вул. Зеленого Мирона 13","queue_code":"GPV3.2","queue_name":"3.2"},
# {"city":"м. Біла Церква","street":"вул. Рибна 32","queue_code":"GPV4.1","queue_name":"4.1"},
# {"city":"м. Біла Церква","street":"вул. Шевченка 4","queue_code":"GPV4.2","queue_name":"4.2"},

{"city":"м. Біла Церква","street":"вул. Героїв Небесної Сотні","queue_code":"GPV5.1","queue_name":"5.1"},

# {"city":"м. Біла Церква","street":"вул. Глибочицька 18","queue_code":"GPV5.2","queue_name":"5.2"},
# {"city":"м. Біла Церква","street":"вул. Сухоярська 4","queue_code":"GPV6.1","queue_name":"6.1"},
# {"city":"м. Вишневе","street":"вул. Гоголя 2","queue_code":"GPV6.2","queue_name":"6.2"},

]


# ── Telegram ───────────────────────────────

def send_message(text):

    try:

        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=20
        )

        log.info("MESSAGE SENT %s", r.status_code)

    except Exception as e:

        log.error("SEND ERROR %s", e)


# ── Files ──────────────────────────────────

def load_file(path):

    if not os.path.exists(path):
        return None

    with open(path) as f:
        return f.read().strip()


def save_file(path, value):

    with open(path, "w") as f:
        f.write(value)


def load_reminders():

    if not os.path.exists(REMINDER_FILE):
        return set()

    with open(REMINDER_FILE) as f:
        return set(f.read().splitlines())


def save_reminder(key):

    with open(REMINDER_FILE, "a") as f:
        f.write(key + "\n")


# ── Helpers ───────────────────────────────

def format_time(minutes):

    return f"{minutes//60:02d}:{minutes%60:02d}"


def get_csrf(html):

    m = re.search(r'csrf-token" content="([^"]+)"', html)

    if not m:
        m = re.search(r'content="([^"]+)" name="csrf-token"', html)

    if not m:
        return None

    return m.group(1)


def build_intervals(data):

    intervals = []
    current = None

    for hour in range(1,25):

        status = data.get(str(hour))

        if status in ["no","first","second"]:

            start = (hour-1)*60
            end = hour*60

            if status == "first":
                end = start + 30

            elif status == "second":
                start += 30

            if current and start == current[1]:

                current[1] = end

            else:

                if current:
                    intervals.append(current)

                current = [start,end]

        else:

            if current:
                intervals.append(current)
                current = None

    if current:
        intervals.append(current)

    return intervals


# ── Main process ───────────────────────────

def process():

    session = requests.Session()

    r1 = session.get(BASE_URL)

    csrf = get_csrf(r1.text)

    if not csrf:

        log.error("CSRF NOT FOUND")
        return


    headers = {
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL
    }


    now = datetime.now(KYIV_TZ)

    now_minutes = now.hour*60 + now.minute

    off_groups = {}
    reminder_groups = {}

    for address in ADDRESSES:

        payload = {
            "method":"getHomeNum",
            "data[0][name]":"city",
            "data[0][value]":address["city"],
            "data[1][name]":"street",
            "data[1][value]":address["street"],
            "data[2][name]":"updateFact",
            "data[2][value]":now.strftime("%H:%M %d.%m.%Y")
        }

        r2 = session.post(API_URL,data=payload,headers=headers)

        data = r2.json()

        if "fact" not in data:
            continue

        all_days = data["fact"]["data"]

        today = sorted(all_days.keys())[0]

        fact_today = all_days[today][address["queue_code"]]

        intervals = build_intervals(fact_today)

        future = [(s,e) for s,e in intervals if e > now_minutes]

        for s,e in future:

            key = f"{s}-{e}"

            off_groups.setdefault(key,[]).append(address["queue_name"])

            diff = s - now_minutes

            if 55 <= diff <= 65:

                reminder_groups.setdefault(key,[]).append(address["queue_name"])


        time.sleep(2)


    lines = []

    for key,queues in sorted(off_groups.items()):

        s,e = map(int,key.split("-"))

        queues = sorted(queues)

        lines.append(
            f"🔴 {format_time(s)}–{format_time(e)} | черги {', '.join(queues)}"
        )


    if not lines:

        schedule = "🟢 Світло є до кінця доби"

    else:

        schedule = "\n".join(lines)


    final = f"📊 Оновлено графік\n\n{schedule}"


    today = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")

    new_hash = hashlib.md5((today + final).encode()).hexdigest()

    old_hash = load_file(STATE_FILE)


    if new_hash != old_hash:

        send_message(final)

        save_file(STATE_FILE,new_hash)

        log.info("GRAPH UPDATED")


    reminders = load_reminders()

    for key,queues in reminder_groups.items():

        s,e = map(int,key.split("-"))

        rkey = f"{today}_{key}"

        if rkey in reminders:
            continue

        text = (
            "⚠️ Через 1 годину відключення світла\n\n"
            f"🔴 {format_time(s)}–{format_time(e)}\n"
            f"Черги: {', '.join(sorted(queues))}"
        )

        send_message(text)

        save_reminder(rkey)


# ── LOOP ───────────────────────────────────

if __name__ == "__main__":

    while True:

        try:

            log.info("CHECK GRAPH")

            process()

        except Exception as e:

            log.error("ERROR %s", e)

        time.sleep(900)
