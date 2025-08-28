import os
import secrets
import requests
import hashlib
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://astf.vercel.app"],  # домен фронтенда
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TERMINAL_KEY = os.getenv("TERMINAL_KEY", "1691507148627DEMO")
SECRET_KEY = os.getenv("SECRET_KEY", "bm5fjkoz0s5vw87j")  # для теста

class PaymentRequest(BaseModel):
    orderId: str
    amount: int
    description: str
    email: str
    customerKey: str  # уникальный id пользователя

class ChargeRequest(BaseModel):
    amount: int
    rebillId: str
    customerKey: str

def generate_token(data: dict) -> str:
    data_with_password = {**data, "Password": SECRET_KEY}
    token_string = ''.join(str(v) for _, v in sorted(data_with_password.items()))
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()

# 1️⃣ Первый платёж — инициализация подписки
@app.post("/init-payment")
def init_payment(payload: PaymentRequest):
    data = {
        "TerminalKey": TERMINAL_KEY,
        "Amount": payload.amount,
        "OrderId": payload.orderId,
        "Description": payload.description,
        "CustomerEmail": payload.email,
        "CustomerKey": payload.customerKey,
        "Recurrent": "Y"
    }
    data["Token"] = generate_token(data)
    try:
        r = requests.post("https://securepay.tinkoff.ru/v2/Init", json=data, timeout=10)
        r.raise_for_status()
        return r.json()  # в ответе придёт PaymentURL
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}

# 2️⃣ Ежемесячное автосписание
@app.post("/charge")
def charge_payment(payload: ChargeRequest):
    data = {
        "TerminalKey": TERMINAL_KEY,
        "Amount": payload.amount,
        "RebillId": payload.rebillId,
        "CustomerKey": payload.customerKey
    }
    data["Token"] = generate_token(data)
    try:
        r = requests.post("https://securepay.tinkoff.ru/v2/Charge", json=data, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}

# 3️⃣ Обработка уведомлений от Tinkoff
@app.post("/tinkoff-callback")
async def tinkoff_callback(payload: dict):
    received_token = payload.get("Token")
    if not received_token:
        return {"Success": False, "error": "No token"}
    payload_copy = payload.copy()
    payload_copy.pop("Token", None)
    expected_token = generate_token(payload_copy)
    if not secrets.compare_digest(received_token, expected_token):
        return {"Success": False, "error": "Invalid token"}

    status = payload.get("Status")
    order_id = payload.get("OrderId")
    rebill_id = payload.get("RebillId")

    # 📌 Здесь — логика сохранения статуса в БД
    # if status == "CONFIRMED" and rebill_id:
    #     save_rebill_id(customer_key, rebill_id)

    return {"Success": True}

