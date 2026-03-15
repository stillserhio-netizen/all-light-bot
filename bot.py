import requests
import re
import hashlib
import time
import os
import json
import logging
import subprocess

from datetime import datetime
from zoneinfo import ZoneInfo


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)


BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


# ── ADDRESSES ─────────────────────────────

ADDRESSES = [

# --- ACTIVE GROUP (chunk 0) ---

{"city":"м. Богуслав","street":"вул. Теліги Олени","queue_code":"GPV1.2","queue_name":"1.2"},
{"city":"м. Біла Церква","street":"вул. Гончара Олеся 2","queue_code":"GPV2.1","queue_name":"2.1"},
{"city":"м. Біла Церква","street":"вул. Героїв Небесної Сотні","queue_code":"GPV5.1","queue_name":"5.1"},


# --- OTHER GROUPS (НЕ використовуються) ---

# {"city":"с. Карапиші","street":"вул. Молодіжна 12","queue_code":"GPV1.1","queue_name":"1.1"},
# {"city":"м. Біла Церква","street":"вул. Голуба Професора","queue_code":"GPV2.2","queue_name":"2.2"},
# {"city":"м. Миронівка","street":"вул. Шевченка 2","queue_code":"GPV3.1","queue_name":"3.1"},
# {"city":"м. Миронівка","street":"вул. Зеленого Мирона 13","queue_code":"GPV3.2","queue_name":"3.2"},
# {"city":"м. Біла Церква","street":"вул. Рибна 32","queue_code":"GPV4.1","queue_name":"4.1"},
# {"city":"м. Біла Церква","street":"вул. Шевченка 4","queue_code":"GPV4.2","queue_name":"4.2"},
# {"city":"м. Біла Церква","street":"вул. Глибочицька 18","queue_code":"GPV5.2","queue_name":"5.2"},
# {"city":"м. Біла Церква","street":"вул. Сухоярська 4","queue_code":"GPV6.1","queue_name":"6.1"},
# {"city":"м. Вишневе","street":"вул. Гоголя 2","queue_code":"GPV6.2","queue_name":"6.2"},

]


# ── CHUNK SETTINGS ─────────────────────────

CHUNK_SIZE = 3
CHUNK = 0

ACTIVE_ADDRESSES = ADDRESSES[CHUNK*CHUNK_SIZE:(CHUNK+1)*CHUNK_SIZE]


# ── TELEGRAM ───────────────────────────────

def send_message(text):

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text}
    )

    log.info("Telegram status %s", r.status_code)


# ── STATE ──────────────────────────────────

def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE) as f:
        return f.read().strip()


def save_state(value):

    with open(STATE_FILE, "w") as f:
        f.write(value)

    log.info("STATE SAVED")


def commit_state():

    try:

        subprocess.run(["git","config","--global","user.name","bot"])
        subprocess.run(["git","config","--global","user.email","bot@bot"])

        subprocess.run(["git","add","state.txt"])
        subprocess.run(["git","commit","-m","update state"], check=False)
        subprocess.run(["git","push"], check=False)

        log.info("STATE PUSHED TO GIT")

    except Exception as e:

        log.error("Git push error %s", e)


# ── HELPERS ────────────────────────────────

def format_time(m):

    return f"{m//60:02d}:{m%60:02d}"


def get_csrf(html):

    m = re.search(r'csrf-token" content="([^"]+)"', html)

    if m:
        return m.group(1)

    m = re.search(r'content="([^"]+)" name="csrf-token"', html)

    if m:
        return m.group(1)

    return None


def build_intervals(data):

    intervals = []
    current = None

    for hour in range(1,25):

        status = data.get(str(hour))

        if status in ["no","first","second"]:

            start = (hour-1)*60
            end = hour*60

            if status=="first":
                end=start+30

            elif status=="second":
                start+=30

            if current and start==current[1]:
                current[1]=end

            else:

                if current:
                    intervals.append(current)

                current=[start,end]

        else:

            if current:
                intervals.append(current)
                current=None

    if current:
        intervals.append(current)

    return intervals


# ── MAIN ───────────────────────────────────

def process():

    session=requests.Session()

    r1=session.get(BASE_URL)

    csrf=get_csrf(r1.text)

    if not csrf:

        log.error("CSRF NOT FOUND")

        return


    headers={
        "X-CSRF-Token":csrf,
        "X-Requested-With":"XMLHttpRequest",
        "Referer":BASE_URL
    }


    off_groups={}

    now = datetime.now(KYIV_TZ)


    for address in ACTIVE_ADDRESSES:

        log.info("REQUEST %s", address["queue_name"])

        payload={
            "method":"getHomeNum",
            "data[0][name]":"city",
            "data[0][value]":address["city"],
            "data[1][name]":"street",
            "data[1][value]":address["street"],
            "data[2][name]":"updateFact",
            "data[2][value]":now.strftime("%H:%M %d.%m.%Y")
        }

        r2=session.post(API_URL,data=payload,headers=headers)

        data=r2.json()

        if "fact" not in data:

            log.error("NO FACT FIELD %s", data)

            continue


        fact=data["fact"]["data"]

        today=sorted(fact.keys(),key=int)[0]

        schedule=fact[today][address["queue_code"]]

        intervals=build_intervals(schedule)


        for s,e in intervals:

            key=f"{s}-{e}"

            off_groups.setdefault(key,[]).append(address["queue_name"])


        time.sleep(2)


    lines=[]


    for key in sorted(off_groups.keys()):

        s,e=map(int,key.split("-"))

        queues=sorted(off_groups[key])

        lines.append(
            f"🔴 {format_time(s)}–{format_time(e)} | черги {', '.join(queues)}"
        )


    if lines:

        text="📊 Оновлено графік\n\n"+ "\n".join(lines)

    else:

        text="📊 Оновлено графік\n\n🟢 Світло є до кінця доби"


    new_hash=hashlib.md5(text.encode()).hexdigest()

    old_hash=load_state()


    log.info("NEW HASH %s", new_hash)

    log.info("OLD HASH %s", old_hash)


    if new_hash==old_hash:

        log.info("NO CHANGE")

        return


    send_message(text)

    save_state(new_hash)

    commit_state()


# ── START ──────────────────────────────────

if __name__ == "__main__":

    process()
