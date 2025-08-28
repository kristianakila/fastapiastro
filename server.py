import os
import secrets
import requests
import hashlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

# Загружаем ключи из переменных окружения
TERMINAL_KEY = os.getenv("TERMINAL_KEY", "1691507148627DEMO")
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY не установлена в переменных окружения!")

class PaymentRequest(BaseModel):
    orderId: str
    amount: int
    description: str
    email: str

def generate_token(data: dict) -> str:
    # Сортируем все ключи, добавляем Password
    token_string = ''.join(str(v) for _, v in sorted({**data, "Password": SECRET_KEY}.items()))
    return hashlib.sha256(token_string.encode('utf-8')).hexdigest()

@app.post("/init-payment")
def init_payment(payload: PaymentRequest):
    data = {
        "TerminalKey": TERMINAL_KEY,
        "Amount": payload.amount,
        "OrderId": payload.orderId,
        "Description": payload.description,
        "CustomerEmail": payload.email
    }
    data["Token"] = generate_token(data)

    try:
        r = requests.post("https://securepay.tinkoff.ru/v2/Init", json=data, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}

@app.post("/tinkoff-callback")
async def tinkoff_callback(payload: dict):
    received_token = payload.get("Token")
    if not received_token:
        return {"Success": False, "error": "No token"}

    # Убираем Token перед расчётом
    payload_copy = payload.copy()
    payload_copy.pop("Token", None)

    expected_token = generate_token(payload_copy)

    # Защищённое сравнение
    if not secrets.compare_digest(received_token, expected_token):
        return {"Success": False, "error": "Invalid token"}

    # Пример обработки статуса
    status = payload.get("Status")
    order_id = payload.get("OrderId")

    # Здесь — ваша бизнес-логика: обновить подписку, записать в БД и т.д.
    # Например:
    # if status == "CONFIRMED":
    #     update_subscription(order_id)

    return {"Success": True}
