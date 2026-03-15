import requests
import re
import hashlib
import time
import os
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


# ── ACTIVE QUEUES ─────────────────────────

QUEUES = {
    "GPV1.2": "1.2",
    "GPV2.1": "2.1",
    "GPV5.1": "5.1",
}


# ── ADDRESS ДЛЯ ОДНОГО ЗАПИТУ ────────────
# (використовується лише щоб отримати JSON)

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"


# ── Telegram ─────────────────────────────

def send_message(text):

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )

    log.info("Telegram status %s", r.status_code)


# ── State ────────────────────────────────

def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE) as f:
        v = f.read().strip()

        if v == "":
            return None

        return v


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


# ── Helpers ──────────────────────────────

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

            if status == "first":
                end = start+30

            elif status == "second":
                start += 30

            if current and start == current[1]:
                current[1] = end

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


# ── MAIN ─────────────────────────────────

def process():

    session=requests.Session()

    # невелика пауза щоб не банив DTEK
    time.sleep(5)

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


    payload={
        "method":"getHomeNum",
        "data[0][name]":"city",
        "data[0][value]":CITY,
        "data[1][name]":"street",
        "data[1][value]":STREET,
        "data[2][name]":"updateFact",
        "data[2][value]":datetime.now(KYIV_TZ).strftime("%H:%M %d.%m.%Y")
    }


    r2=session.post(API_URL,data=payload,headers=headers)

    try:
        data=r2.json()
    except:
        log.error("JSON ERROR")
        return


    if "fact" not in data:

        log.error("NO FACT FIELD %s", data)

        return


    fact=data["fact"]["data"]

    today=sorted(fact.keys(),key=int)[0]

    schedule=fact[today]


    off_groups={}


    for code,name in QUEUES.items():

        queue_data=schedule.get(code)

        if not queue_data:
            continue

        intervals=build_intervals(queue_data)

        for s,e in intervals:

            key=f"{s}-{e}"

            off_groups.setdefault(key,[]).append(name)


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


    if old_hash is None:

        log.info("FIRST RUN -> SAVE STATE")

        save_state(new_hash)

        commit_state()

        return


    if new_hash == old_hash:

        log.info("NO CHANGE")

        return


    send_message(text)

    save_state(new_hash)

    commit_state()


if __name__ == "__main__":

    process()
