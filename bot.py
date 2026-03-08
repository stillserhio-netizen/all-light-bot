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
STATE_TOMORROW = "state_tomorrow.txt"
REMINDER_FILE = "reminders.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


ADDRESSES = [

{"city":"с. Карапиші","street":"вул. Молодіжна 12","queue_code":"GPV1.1","queue_name":"1.1"},
{"city":"м. Богуслав","street":"вул. Теліги Олени","queue_code":"GPV1.2","queue_name":"1.2"},
{"city":"м. Біла Церква","street":"вул. Гончара Олеся 2","queue_code":"GPV2.1","queue_name":"2.1"},
{"city":"м. Біла Церква","street":"вул. Голуба Професора","queue_code":"GPV2.2","queue_name":"2.2"},
{"city":"м. Миронівка","street":"вул. Шевченка 2","queue_code":"GPV3.1","queue_name":"3.1"},
{"city":"м. Миронівка","street":"вул. Зеленого Мирона 13","queue_code":"GPV3.2","queue_name":"3.2"},
{"city":"м. Біла Церква","street":"вул. Рибна 32","queue_code":"GPV4.1","queue_name":"4.1"},
{"city":"м. Біла Церква","street":"вул. Шевченка 4","queue_code":"GPV4.2","queue_name":"4.2"},
{"city":"м. Біла Церква","street":"вул. Героїв Небесної Сотні","queue_code":"GPV5.1","queue_name":"5.1"},
{"city":"м. Біла Церква","street":"вул. Глибочицька 18","queue_code":"GPV5.2","queue_name":"5.2"},
{"city":"м. Біла Церква","street":"вул. Сухоярська 4","queue_code":"GPV6.1","queue_name":"6.1"},
{"city":"м. Вишневе","street":"вул. Гоголя 2","queue_code":"GPV6.2","queue_name":"6.2"}

]


def send_message(text):

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text}
    )


def load_file(path):

    if not os.path.exists(path):
        return None

    with open(path) as f:
        return f.read().strip()


def save_file(path,value):

    with open(path,"w") as f:
        f.write(value)


def load_reminders():

    if not os.path.exists(REMINDER_FILE):
        return set()

    with open(REMINDER_FILE) as f:
        return set(f.read().splitlines())


def save_reminder(key):

    with open(REMINDER_FILE,"a") as f:
        f.write(key+"\n")


def commit_state():

    subprocess.run(["git","config","--global","user.name","bot"])
    subprocess.run(["git","config","--global","user.email","bot@github"])

    subprocess.run(["git","add","."])
    subprocess.run(["git","commit","-m","update state"],check=False)
    subprocess.run(["git","push"],check=False)


def format_time(minutes):

    return f"{minutes//60:02d}:{minutes%60:02d}"


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


def get_csrf(html):

    m=re.search(r'csrf-token" content="([^"]+)"',html)

    if not m:
        m=re.search(r'content="([^"]+)" name="csrf-token"',html)

    if not m:
        return None

    return m.group(1)


def process():

    session=requests.Session()

    r1=session.get(BASE_URL,headers={"User-Agent":"Mozilla/5.0"})

    if r1.status_code!=200:
        return

    csrf=get_csrf(r1.text)

    if not csrf:
        return


    headers_post={
        "User-Agent":"Mozilla/5.0",
        "X-Requested-With":"XMLHttpRequest",
        "Referer":BASE_URL,
        "Origin":"https://www.dtek-krem.com.ua",
        "X-CSRF-Token":csrf
    }


    now=datetime.now(KYIV_TZ)
    now_minutes=now.hour*60+now.minute

    off_groups={}
    reminder_groups={}
    tomorrow_groups={}


    for address in ADDRESSES:

        payload={
            "method":"getHomeNum",
            "data[0][name]":"city",
            "data[0][value]":address["city"],
            "data[1][name]":"street",
            "data[1][value]":address["street"],
            "data[2][name]":"updateFact",
            "data[2][value]":now.strftime("%H:%M %d.%m.%Y")
        }

        r2=session.post(API_URL,data=payload,headers=headers_post)

        if r2.status_code!=200:
            continue

        data=r2.json()

        if "fact" not in data:
            continue


        all_days=data["fact"]["data"]

        timestamps=sorted(all_days.keys(),key=int)

        today_ts=timestamps[0]

        tomorrow_ts=None
        if len(timestamps)>1:
            tomorrow_ts=timestamps[1]


        fact_today=all_days[today_ts][address["queue_code"]]

        intervals=build_intervals(fact_today)

        future=[(s,e) for s,e in intervals if e>now_minutes]


        for s,e in future:

            key=f"{s}-{e}"

            off_groups.setdefault(key,[]).append(address["queue_name"])

            diff=s-now_minutes

            if 55<=diff<=65:
                reminder_groups.setdefault(key,[]).append(address["queue_name"])


        if tomorrow_ts:

            fact_tomorrow=all_days[tomorrow_ts][address["queue_code"]]

            intervals_tomorrow=build_intervals(fact_tomorrow)

            for s,e in intervals_tomorrow:

                key=f"{s}-{e}"

                tomorrow_groups.setdefault(key,[]).append(address["queue_name"])


        time.sleep(0.5)


    off_lines=[]

    for key,queues in off_groups.items():

        s,e=map(int,key.split("-"))

        off_lines.append(
            f"Черга {', '.join(queues)} — {format_time(s)}–{format_time(e)}"
        )


    if off_lines:

        final=(
            "📊 Оновлено графік\n\n"
            "🔴 Відключення:\n"
            + "\n".join(off_lines)
            + "\n\n🟢 Інші черги — світло є"
        )

    else:

        final="📊 Оновлено графік\n\n🟢 Світло є до кінця доби"


    today=datetime.now(KYIV_TZ).strftime("%Y-%m-%d")

    new_hash=hashlib.md5((today+final).encode()).hexdigest()

    old_hash=load_file(STATE_FILE)


    if new_hash!=old_hash:

        send_message(final)

        save_file(STATE_FILE,new_hash)

        commit_state()


    reminders=load_reminders()


    for key,queues in reminder_groups.items():

        s,e=map(int,key.split("-"))

        rkey=f"{today}_{key}"

        if rkey in reminders:
            continue


        text=(
            "⚠️ Через 1 годину відключення світла\n\n"
            f"🔴 {format_time(s)}–{format_time(e)}\n"
            f"Черги: {', '.join(queues)}"
        )

        send_message(text)

        save_reminder(rkey)


    if tomorrow_groups and now.hour>=18:

        tomorrow_lines=[]

        for key,queues in tomorrow_groups.items():

            s,e=map(int,key.split("-"))

            tomorrow_lines.append(
                f"Черга {', '.join(queues)} — {format_time(s)}–{format_time(e)}"
            )


        tomorrow_text=(
            "📅 Графік на завтра\n\n"
            "🔴 Відключення:\n"
            + "\n".join(tomorrow_lines)
        )


        thash=hashlib.md5((today+tomorrow_text).encode()).hexdigest()

        old=load_file(STATE_TOMORROW)

        if thash!=old:

            send_message(tomorrow_text)

            save_file(STATE_TOMORROW,thash)

            commit_state()


while True:

    try:
        process()
    except Exception as e:
        print("ERROR:",e)

    time.sleep(600)
