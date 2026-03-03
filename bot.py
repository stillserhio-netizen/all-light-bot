import requests

BOT_TOKEN = "8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs"
CHAT_ID = "-1003802691352"

def main():
    requests.post(
        f"https://api.telegram.org/bot8531283640:AAGcDueeQqu-nXZ8aYrBT7lh8lABOWi9Crs/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": "Бот запущено з GitHub ✅"
        }
    )

if __name__ == "__main__":
    main()
