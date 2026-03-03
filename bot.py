import requests
from datetime import datetime

BASE_URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE_URL
}

def main():
    session = requests.Session()

    # 1. Отримуємо cookies
    session.get(BASE_URL, headers=HEADERS, timeout=20)

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

    r = session.post(
        API_URL,
        data=payload,
        headers=HEADERS,
        timeout=20
    )

    print("STATUS:", r.status_code)
    print("TEXT:", r.text)

if __name__ == "__main__":
    main()
