import requests
import re
import hashlib
import time
import os
import threading
import http.server
import socketserver
from datetime import datetime
from zoneinfo import ZoneInfo


BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

STATE_FILE = "state.txt"
REMINDER_FILE = "reminders.txt"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


ADDRESSES = [

{"city":"с. Карапиші","street":"вул. Молодіжна","queue_code":"GPV1.1","queue_name":"Черга 1.1"},
{"city":"м. Богуслав","street":"вул. Теліги Олени","queue_code":"GPV1.2","queue_name":"Черга 1.2"},

{"city":"м. Біла Церква","street":"вул. Гончара Олеся","queue_code":"GPV2.1","queue_name":"Черга 2.1"},
{"city":"м. Біла Церква","street":"вул. Голуба Професора","queue_code":"GPV2.2","queue_name":"Черга 2.2"},

{"city":"м. Миронівка","street":"вул. Шевченка","queue_code":"GPV3.1","queue_name":"Черга 3.1"},
{"city":"м. Миронівка","street":"вул. Зеленого Мирона","queue_code":"GPV3.2","queue_name":"Черга 3.2"},

{"city":"м. Біла Церква","street":"вул. Рибна","queue_code":"GPV4.1","queue_name":"Черга 4.1"},
{"city":"м. Біла Церква","street":"вул. Шевченка","queue_code":"GPV4.2","queue_name":"Черга 4.2"},

{"city":"м. Біла Церква","street":"вул. Героїв Небесної Сотні","queue_code":"GPV5.1","queue_name":"Черга 5.1"},
{"city":"м. Біла Церква","street":"вул. Глибочицька","queue_code":"GPV5.2","queue_name":"Черга 5.2"},

{"city":"м. Біла Церква","street":"вул. Сухоярська","queue_code":"GPV6.1","queue_name":"Черга 6.1"},
{"city":"м. Вишневе","street":"вул. Гоголя","queue_code":"GPV6.2","queue_name":"Черга 6.2"}

]


# ================= HTTP SERVER =================

class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def keep_alive():

    port = int(os.environ.get("PORT", 10000))

    with socketserver.TCPServer(("", port), Handler) as httpd:

        print("HTTP SERVER STARTED ON", port)

        httpd.serve_forever()


# ================= TELEGRAM =================

def send_message(text):

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text}
    )

    print("TELEGRAM STATUS:", r.status_code)


# ================= FILES =================

def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE,"r") as f:
        return f.read().strip()


def save_state(value):

    with open(STATE_FILE,"w") as f:
        f.write(value)


def load_reminders():

    if not os.path.exists(REMINDER_FILE):
        return set()

    with open(REMINDER_FILE,"r") as f:
        return set(f.read().splitlines())


def save_reminder(value):

    with open(REMINDER_FILE,"a") as f:
        f.write(value+"\n")


# ================= HELPERS =================

def format_time(minutes):

    return f"{minutes//60:02d}:{minutes%60:02d}"


def build_intervals(fact_data):

    intervals=[]
    current=None

    for hour in range(1,25):

        status=fact_data.get(str(hour))

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


# ================= MAIN =================

def main():

    print("BOT START")

    session=requests.Session()

    r1=session.get(BASE_URL,headers={"User-Agent":"Mozilla/5.0"})

    print("GET STATUS:",r1.status_code)

    if r1.status_code!=200:
        return

    csrf=get_csrf(r1.text)

    if not csrf:
        print("CSRF NOT FOUND")
        return


    headers={
        "User-Agent":"Mozilla/5.0",
        "X-Requested-With":"XMLHttpRequest",
        "Referer":BASE_URL,
        "Origin":"https://www.dtek-krem.com.ua",
        "X-CSRF-Token":csrf
    }


    now=datetime.now(KYIV_TZ)
    now_minutes=now.hour*60+now.minute

    reminders=load_reminders()

    off_blocks=[]


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

        r2=session.post(API_URL,data=payload,headers=headers)

        if r2.status_code!=200:
            continue

        data=r2.json()

        if "fact" not in data:
            continue

        all_days=data["fact"]["data"]

        timestamps=sorted(all_days.keys(),key=int)

        today_ts=timestamps[0]

        fact_today=all_days[today_ts][address["queue_code"]]

        intervals=build_intervals(fact_today)

        future=[(s,e) for s,e in intervals if e>now_minutes]

        if future:

            for s,e in future:

                off_blocks.append(
                    f"{address['queue_name']} — {format_time(s)}–{format_time(e)}"
                )

                diff=s-now_minutes

                if 55<=diff<=65:

                    key=f"{address['queue_code']}_{s}"

                    if key not in reminders:

                        send_message(
f"""⚠️ Через 1 годину відключення світла

{address['queue_name']}
🔴 {format_time(s)}–{format_time(e)}"""
                        )

                        save_reminder(key)

        time.sleep(0.5)


    if off_blocks:

        final_message=(
            "📊 Оновлено графік\n\n"
            "🔴 Відключення:\n"
            + "\n".join(off_blocks) +
            "\n\n🟢 Інші черги — світло є"
        )

    else:

        final_message=(
            "📊 Оновлено графік\n\n"
            "🟢 До кінця доби світло буде"
        )


    new_hash=hashlib.md5(final_message.encode()).hexdigest()
    old_hash=load_state()

    if old_hash is None or new_hash!=old_hash:

        send_message(final_message)

        save_state(new_hash)


# ================= LOOP =================

def runner():

    while True:

        try:

            main()

        except Exception as e:

            print("ERROR:", e)

        print("SLEEP 15 MIN")

        time.sleep(900)


# ================= START =================

threading.Thread(target=keep_alive).start()

runner()
