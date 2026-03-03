import requests
from datetime import datetime

API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.dtek-krem.com.ua/ua/shutdowns"
}

def main():
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

    r = requests.post(
        API_URL,
        data=payload,
        headers=HEADERS,
        timeout=20
    )

    print("STATUS:", r.status_code)
    print("TEXT:", r.text)

if __name__ == "__main__":
    main()
