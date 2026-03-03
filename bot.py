import requests
from datetime import datetime

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

HEADERS_GET = {
    "User-Agent": "Mozilla/5.0"
}

HEADERS_POST = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.dtek-krem.com.ua",
    "Referer": BASE_URL,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
}

def main():
    session = requests.Session()

    # 1. Отримати сторінку щоб зловити cookies
    r1 = session.get(BASE_URL, headers=HEADERS_GET, timeout=20)
    print("GET STATUS:", r1.status_code)

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    payload = {
        "method": "getHomeNum",
        "data[0][name]": "city",
        "data[0][value]": CITY,
        "data[1][name]": "street",
        "data[1][value]": STREET,
        "data[2][name]": "updateFact",
        "data[2][value]": now_str
    }

    r2 = session.post(
        API_URL,
        data=payload,
        headers=HEADERS_POST,
        timeout=20
    )

    print("POST STATUS:", r2.status_code)
    print("RESPONSE:", r2.text)

if __name__ == "__main__":
    main()
