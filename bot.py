import requests
import re
import hashlib
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont


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


def send_photo(path, caption):

    with open(path, "rb") as f:

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": caption
            },
            files={"photo": f}
        )


def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE, "r") as f:
        return f.read().strip()


def save_state(value):

    with open(STATE_FILE, "w") as f:
        f.write(value)


def format_time(minutes):

    h = minutes // 60
    m = minutes % 60

    return f"{h:02d}:{m:02d}"


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


def get_color(status):

    if status == "yes":
        return (120, 200, 120)

    if status in ["no", "first", "second"]:
        return (230, 70, 70)

    if status in ["maybe", "mfirst", "msecond"]:
        return (240, 200, 80)

    return (200, 200, 200)


def draw_table(results):

    cell_w = 40
    cell_h = 40

    width = 200 + cell_w * 24
    height = 120 + cell_h * len(results)

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except:
        font = ImageFont.load_default()

    for h in range(24):

        x = 200 + h * cell_w

        draw.text((x + 10, 40), str(h), fill="black", font=font)

    y = 80

    for queue_name, fact in results:

        draw.text((20, y + 10), queue_name, fill="black", font=font)

        for hour in range(1, 25):

            status = fact.get(str(hour), "yes")

            color = get_color(status)

            x = 200 + (hour - 1) * cell_w

            draw.rectangle(
                (x, y, x + cell_w, y + cell_h),
                fill=color,
                outline="black"
            )

        y += cell_h

    img.save("schedule.png")

    return "schedule.png"


def check_schedule():

    session = requests.Session()

    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})

    if r1.status_code != 200:
        return None, None

    csrf_match = re.search(r'name="csrf-token" content="(.+?)"', r1.text)

    if not csrf_match:
        return None, None

    csrf_token = csrf_match.group(1)

    headers_post = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf_token
    }

    now = datetime.now(KYIV_TZ)

    message_blocks = []
    results = []

    current_minutes = now.hour * 60 + now.minute

    for address in ADDRESSES:

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

        if r2.status_code != 200:
            continue

        data = r2.json()

        if "fact" not in data:
            continue

        all_days = data["fact"]["data"]

        timestamps = sorted(all_days.keys(), key=int)

        today_ts = timestamps[0]

        fact = all_days[today_ts][address["queue_code"]]

        intervals = build_intervals(fact)

        future = [(s, e) for s, e in intervals if e > current_minutes]

        block = f"{address['queue_name']}\n"

        if future:
            for s, e in future:
                block += f"{format_time(s)}–{format_time(e)}\n"
        else:
            block += "До кінця доби світло буде\n"

        message_blocks.append(block.strip())

        results.append((address["queue_name"], fact))

        time.sleep(2)

    text_message = "\n\n".join(message_blocks)

    graph = draw_table(results)

    return graph, text_message


def main():

    print("BOT STARTED")

    while True:

        try:

            graph_file, text_message = check_schedule()

            if graph_file is None:
                time.sleep(900)
                continue

            new_hash = hashlib.md5(text_message.encode()).hexdigest()

            old_hash = load_state()

            if old_hash is None:

                print("FIRST RUN SEND")

                send_photo(graph_file, text_message)

                save_state(new_hash)

            elif new_hash != old_hash:

                print("GRAPH CHANGED")

                send_photo(graph_file, text_message)

                save_state(new_hash)

            else:

                print("NO CHANGES")

        except Exception as e:

            print("ERROR:", e)

        print("SLEEP 15 MIN")

        time.sleep(900)


if __name__ == "__main__":
    main()
