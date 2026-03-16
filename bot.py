import requests
import re
import hashlib
import os
import logging
import subprocess
import time

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


QUEUES = [

    ("GPV1.2","1.2","м. Богуслав","вул. Теліги Олени"),
    ("GPV2.1","2.1","м. Біла Церква","вул. Гончара Олеся 2"),
    ("GPV5.1","5.1","м. Біла Церква","вул. Героїв Небесної Сотні"),

]


def send_message(text):

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )

    log.info("Telegram status %s", r.status_code)


def get_last_message():

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    r = requests.get(url).json()

    if not r["result"]:
        return None

    for item in reversed(r["result"]):

        msg = item.get("channel_post")

        if msg and str(msg["chat"]["id"]) == CHAT_ID:
            return msg["text"]

    return None


def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE) as f:

        v = f.read().strip()

        if v == "":
            return None

        return v


def save_state(v):

    with open(STATE_FILE,"w") as f:
        f.write(v)

    log.info("STATE SAVED")


def commit_state():

    subprocess.run(["git","config","--global","user.name","bot"])
    subprocess.run(["git","config","--global","user.email","bot@bot"])

    subprocess.run(["git","add","state.txt"])
    subprocess.run(["git","commit","-m","update state"],check=False)
    subprocess.run(["git","push"],check=False)

    log.info("STATE PUSHED")


def format_time(m):

    return f"{m//60:02d}:{m%60:02d}"


def get_csrf(html):

    m = re.search(r'csrf-token" content="([^"]+)"',html)

    if m:
        return m.group(1)

    m = re.search(r'content="([^"]+)" name="csrf-token"',html)

    if m:
        return m.group(1)

    return None


def build_intervals(data):

    intervals=[]
    current=None

    for hour in range(1,25):

        status=data.get(str(hour))

        if status in ["no","first","second"]:

            start=(hour-1)*60
            end=hour*60

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


def process():

    session=requests.Session()

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "uk-UA,uk;q=0.9"
    }

    r1=session.get(BASE_URL,headers=browser_headers)

    csrf=get_csrf(r1.text)

    if not csrf:

        log.warning("CSRF NOT FOUND retry")

        time.sleep(3)

        r1=session.get(BASE_URL,headers=browser_headers)

        csrf=get_csrf(r1.text)

        if not csrf:

            log.error("CSRF STILL NOT FOUND")

            return


    headers={
        "X-CSRF-Token":csrf,
        "X-Requested-With":"XMLHttpRequest",
        "Referer":BASE_URL,
        "User-Agent":browser_headers["User-Agent"]
    }


    now=datetime.now(KYIV_TZ)

    lines=[]


    for code,name,city,street in QUEUES:

        log.info("REQUEST %s",name)

        payload={
            "method":"getHomeNum",
            "data[0][name]":"city",
            "data[0][value]":city,
            "data[1][name]":"street",
            "data[1][value]":street,
            "data[2][name]":"updateFact",
            "data[2][value]":now.strftime("%H:%M %d.%m.%Y")
        }

        r2=session.post(API_URL,data=payload,headers=headers)

        data=r2.json()

        if "fact" not in data:

            lines.append(f"Черга {name}")
            lines.append("🟢Світло є до кінця доби")

            time.sleep(8)
            continue


        fact=data["fact"]["data"]

        today=sorted(fact.keys(),key=int)[0]

        schedule=fact[today].get(code)


        lines.append(f"Черга {name}")


        if not schedule:

            lines.append("🟢Світло є до кінця доби")

            time.sleep(8)
            continue


        intervals=build_intervals(schedule)


        if not intervals:

            lines.append("🟢Світло є до кінця доби")

        else:

            for s,e in intervals:

                lines.append(
                    f"🔴{format_time(s)}-{format_time(e)}"
                )


        time.sleep(8)


    text="📊Оновлено графік\n\n"+"\n".join(lines)


    new_hash=hashlib.md5(text.encode()).hexdigest()

    old_hash=load_state()

    last_message=get_last_message()


    log.info("NEW HASH %s",new_hash)
    log.info("OLD HASH %s",old_hash)


    if last_message == text:

        log.info("SAME AS CHANNEL MESSAGE")

        return


    if old_hash is None:

        save_state(new_hash)
        commit_state()

        return


    if new_hash == old_hash:

        log.info("NO CHANGE")

        return


    send_message(text)

    save_state(new_hash)

    commit_state()


if __name__=="__main__":

    process()
