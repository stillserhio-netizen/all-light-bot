import requests
import re
import hashlib
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

BOT_TOKEN = "YOUR_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

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


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except:
        return None


def save_state(value):
    with open(STATE_FILE, "w") as f:
        f.write(value)


def send_photo(path):

    with open(path, "rb") as f:

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID},
            files={"photo": f}
        )


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


def draw_graph(results):

    width = 1000
    height = 120 + 120 * len(results)

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except:
        font = ImageFont.load_default()

    px_per_min = (width - 200) / 1440

    y = 80

    for name, intervals in results:

        draw.text((40, y - 40), name, fill="black", font=font)

        # фон
        draw.rectangle(
            (160, y, width - 40, y + 40),
            fill=(200, 230, 200)
        )

        # відключення
        for start, end in intervals:

            x1 = 160 + start * px_per_min
            x2 = 160 + end * px_per_min

            draw.rectangle(
                (x1, y, x2, y + 40),
                fill=(220, 60, 60)
            )

        # години
        for h in range(0, 25, 2):

            x = 160 + (h * 60) * px_per_min

            draw.text(
                (x - 10, y + 50),
                str(h),
                fill="black",
                font=font
            )

        y += 120

    img.save("schedule.png")

    return "schedule.png"


def main():

    session = requests.Session()

    r1 = session.get(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})

    if r1.status_code != 200:
        return

    csrf_match = re.search(r'name="csrf-token" content="(.+?)"', r1.text)

    if not csrf_match:
        return

    csrf_token = csrf_match.group(1)

    headers_post = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
        "Origin": "https://www.dtek-krem.com.ua",
        "X-CSRF-Token": csrf_token
    }

    results = []

    now = datetime.now(KYIV_TZ)

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

        intervals = build_intervals(
            all_days[today_ts][address["queue_code"]]
        )

        results.append((address["queue_name"], intervals))

        time.sleep(2)

    graph_file = draw_graph(results)

    data_hash = hashlib.md5(open(graph_file, "rb").read()).hexdigest()

    old_hash = load_state()

    if data_hash != old_hash:

        save_state(data_hash)

        send_photo(graph_file)


if __name__ == "__main__":
    main()
