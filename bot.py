import requests
from datetime import datetime

API_URL = "https://www.dtek-krem.com.ua/ua/ajax"

CITY = "м. Богуслав"
STREET = "вул. Теліги Олени"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest"
}

def main():
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    payload = {
        "method": "getHomeNum",
        "city": CITY,
        "street": STREET,
        "updateFact": now_str
    }

    r = requests.post(API_URL, data=payload, headers=HEADERS, timeout=20)

    print("STATUS:", r.status_code)
    print("TEXT:", r.text)

if __name__ == "__main__":
    main()
