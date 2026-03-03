import requests
import re
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

HEADERS_GET = {
    "User-Agent": "Mozilla/5.0"
}

def main():
    session = requests.Session()

    # 1️⃣ Отримуємо сторінку
    r1 = session.get(BASE_URL, headers=HEADERS_GET, timeout=20)
    print("GET STATUS:", r1.status_code)

    # 2️⃣ Витягуємо CSRF токен з HTML
    csrf_match = re.search(
        r'name="csrf-token" content="(.+?)"',
        r1.text
    )

    if not csrf_match:
        print("CSRF NOT FOUND")
        return

    csrf_token = csrf_match.group(1)
    print("CSRF:", csrf_token)

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

    r2 = session.post(
        API_URL,
        data=payload,
        headers=headers_post,
        timeout=20
    )

    print("POST STATUS:", r2.status_code)
    print("RESPONSE:", r2.text)

if __name__ == "__main__":
    main()
