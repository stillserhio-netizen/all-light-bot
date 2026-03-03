import requests

BASE_URL = "https://www.dtek-krem.com.ua"
AJAX_URL = "https://www.dtek-krem.com.ua/ua/ajax"

def main():

    session = requests.Session()

    print("=== STEP 1: GET /ua/shutdowns ===")
    r = session.get(BASE_URL + "/ua/shutdowns")

    print("GET STATUS:", r.status_code)
    print("GET LENGTH:", len(r.text))
    print("GET HEAD (first 300 chars):")
    print(r.text[:300])
    print()

    print("=== COOKIES RECEIVED ===")
    for cookie in session.cookies:
        print(cookie.name, "=", cookie.value)
    print()

    csrf = session.cookies.get("_csrf-dtek-krem")

    if not csrf:
        print("CSRF cookie NOT FOUND")
        return
    else:
        print("CSRF cookie FOUND:", csrf)
        print()

    print("=== STEP 2: POST /ua/ajax ===")

    headers = {
        "x-csrf-token": csrf,
        "x-requested-with": "XMLHttpRequest",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": BASE_URL,
        "referer": BASE_URL + "/ua/shutdowns",
        "user-agent": "Mozilla/5.0"
    }

    payload = {
        "method": "getSchedule",
        "data[0][name]": "city",
        "data[0][value]": "м. Богуслав"
    }

    r2 = session.post(AJAX_URL, data=payload, headers=headers)

    print("POST STATUS:", r2.status_code)
    print("POST LENGTH:", len(r2.text))
    print("POST HEAD (first 500 chars):")
    print(r2.text[:500])


if __name__ == "__main__":
    main()
