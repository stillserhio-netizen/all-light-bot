import requests
import datetime
import pytz

# ==============================
# ТВОЇ ДАНІ
# ==============================

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"
QUEUE = "GPV1.2"  # Черга 1.2

BASE_URL = "https://www.dtek-krem.com.ua"
AJAX_URL = BASE_URL + "/ua/ajax"


# ==============================
# TELEGRAM
# ==============================

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })


# ==============================
# ОТРИМАННЯ ДАНИХ
# ==============================

def get_schedule():
    session = requests.Session()

    # 1. GET сторінки для отримання cookies
    r = session.get(BASE_URL + "/ua/shutdowns")
    if r.status_code != 200:
        return None

    csrf = session.cookies.get("_csrf-dtek-krem")
    if not csrf:
        return None

    headers = {
        "x-csrf-token": csrf,
        "x-requested-with": "XMLHttpRequest",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": BASE_URL,
        "referer": BASE_URL + "/ua/shutdowns",
        "user-agent": "Mozilla/5.0"
    }

    payload = {
        "method": "getHomeNum",
        "data[0][name]": "city",
        "data[0][value]": CITY,
        "data[1][name]": "street",
        "data[1][value]": STREET,
    }

    r2 = session.post(AJAX_URL, data=payload, headers=headers)

    if r2.status_code != 200:
        return None

    return r2.json()


# ==============================
# ОБРОБКА
# ==============================

def build_intervals(data):
    tz = pytz.timezone("Europe/Kyiv")
    now = datetime.datetime.now(tz)
    today_key = str(data["today"])

    queue_data = data["fact"]["data"][today_key][QUEUE]
    time_map = data["preset"]["time_zone"]
    time_type = data["preset"]["time_type"]

    off_hours = []

    for hour in range(1, 25):
        status = queue_data[str(hour)]

        if status in ["no", "first", "second"]:
            start = time_map[str(hour)][1]
            end = time_map[str(hour)][2]
            off_hours.append((start, end))

    if not off_hours:
        return "🟢Світло буде до кінця доби"

    # Об'єднання інтервалів
    intervals = []
    current_start, current_end = off_hours[0]

    for start, end in off_hours[1:]:
        if start == current_end:
            current_end = end
        else:
            intervals.append((current_start, current_end))
            current_start, current_end = start, end

    intervals.append((current_start, current_end))

    text = "🔴Світла не буде:\n"
    for start, end in intervals:
        text += f"{start}–{end}\n"

    return text.strip()


# ==============================
# MAIN
# ==============================

def main():
    data = get_schedule()

    if not data:
        send_message("Помилка отримання даних")
        return

    message = "Черга 1.2\n" + build_intervals(data)
    send_message(message)


if __name__ == "__main__":
    main()
