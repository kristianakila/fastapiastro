import os
import secrets
import requests
import hashlib
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# Инициализация Firestore из переменной окружения FIREBASE_KEY_JSON
firebase_key_json = os.getenv("FIREBASE_KEY_JSON")
if not firebase_key_json:
    raise RuntimeError("FIREBASE_KEY_JSON is not set in environment variables")

cred = credentials.Certificate(json.loads(firebase_key_json))
firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://astf.vercel.app"],  # домен фронтенда
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TERMINAL_KEY = os.getenv("TERMINAL_KEY", "1691507148627DEMO")
SECRET_KEY = os.getenv("SECRET_KEY", "bm5fjkoz0s5vw87j")  # тестовый

class PaymentRequest(BaseModel):
    orderId: str
    amount: int
    description: str
    email: str
    customerKey: str  # telegram_id пользователя

class ChargeRequest(BaseModel):
    amount: int
    rebillId: str
    customerKey: str

def generate_token(data: dict) -> str:
    data_with_password = {**data, "Password": SECRET_KEY}
    token_string = ''.join(str(v) for _, v in sorted(data_with_password.items()))
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()


# 0️⃣ ОПОВЕЩЕНИЯ
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")

def send_telegram_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не задан")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print(f"📩 Сообщение отправлено {chat_id}")
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)


# 1️⃣ Первый платёж
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
        resp_data = r.json()

        db.collection("telegramUsers").document(payload.customerKey).set({
            "email": payload.email,
            "orderId": payload.orderId,
            "amount": payload.amount,
            "description": payload.description,
            "subscription": {
                "status": "pending",
                "createdAt": firestore.SERVER_TIMESTAMP
            },
            "tinkoff": {
                "PaymentId": resp_data.get("PaymentId"),
                "PaymentURL": resp_data.get("PaymentURL")
            }
        }, merge=True)

        send_telegram_message(
            chat_id=payload.customerKey,
            text=f"✅ Ваш заказ <b>{payload.description}</b> на сумму {payload.amount/100:.2f}₽ создан!"
        )

        if TELEGRAM_ADMIN_ID:
            send_telegram_message(
                chat_id=TELEGRAM_ADMIN_ID,
                text=f"🛒 Новый заказ!\nПользователь: {payload.customerKey}\n"
                     f"Описание: {payload.description}\n"
                     f"Сумма: {payload.amount/100:.2f}₽"
            )

        return resp_data
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
        resp_data = r.json()

        db.collection("telegramUsers").document(payload.customerKey).update({
            "lastCharge": firestore.SERVER_TIMESTAMP,
            "lastChargeResult": resp_data
        })

        return resp_data
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


# 3️⃣ Callback от Tinkoff
from fastapi.responses import RedirectResponse

@app.get("/tinkoff-callback")
async def tinkoff_callback_get(request: Request):
    params = dict(request.query_params)
    print("🌐 BackURL GET params:", params)

    if params.get("Success", "").lower() == "true" and "OrderId" in params:
        order_id = params["OrderId"]
        users_ref = db.collection("telegramUsers").where("orderId", "==", order_id).stream()
        for doc in users_ref:
            expire_at = datetime.utcnow() + timedelta(days=30)
            db.collection("telegramUsers").document(doc.id).update({
                "subscription.status": "Premium",
                "subscription.updatedAt": firestore.SERVER_TIMESTAMP,
                "subscription.expiresAt": expire_at,
                "subscription.lastCallbackPayload": params
            })

            print(f"✅ Статус подписки обновлён для {doc.id}")

            send_telegram_message(
                chat_id=doc.id,
                text="🎉 Оплата прошла успешно! Ваша подписка активирована."
            )

            if TELEGRAM_ADMIN_ID:
                send_telegram_message(
                    chat_id=TELEGRAM_ADMIN_ID,
                    text=f"💳 Оплата подтверждена!\nПользователь: {doc.id}\nOrderId: {order_id}"
                )

    return {
        "info": "BackURL redirect от Tinkoff",
        "params": params
    }


@app.post("/tinkoff-callback")
async def tinkoff_callback_post(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    print("🔥 Callback POST получен:", payload)

    if not payload:
        return {"Success": False, "error": "Empty payload"}

    received_token = payload.get("Token")
    if not received_token:
        if payload.get("CustomerKey"):
            db.collection("telegramUsers").document(payload["CustomerKey"]).update({
                "subscription.lastCallbackError": "No token",
                "subscription.callbackPayload": payload,
                "subscription.updatedAt": firestore.SERVER_TIMESTAMP
            })
        return {"Success": False, "error": "No token"}

    payload_copy = dict(payload)
    payload_copy.pop("Token", None)
    expected_token = generate_token(payload_copy)

    if not secrets.compare_digest(received_token, expected_token):
        if payload.get("CustomerKey"):
            db.collection("telegramUsers").document(payload["CustomerKey"]).update({
                "subscription.lastCallbackError": "Invalid token",
                "subscription.callbackPayload": payload,
                "subscription.updatedAt": firestore.SERVER_TIMESTAMP
            })
        return {"Success": False, "error": "Invalid token"}

    status = payload.get("Status")
    customer_key = payload.get("CustomerKey")
    rebill_id = payload.get("RebillId")

    if customer_key:
        update_data = {
            "subscription.updatedAt": firestore.SERVER_TIMESTAMP,
            "subscription.lastCallbackPayload": payload
        }

        if status and status.lower() == "confirmed":
            expire_at = datetime.utcnow() + timedelta(days=30)
            update_data["subscription.status"] = "Premium"
            update_data["subscription.expiresAt"] = expire_at
        else:
            update_data["subscription.status"] = (status or "").lower()

        if rebill_id:
            update_data["tinkoff.RebillId"] = rebill_id

        db.collection("telegramUsers").document(customer_key).update(update_data)

    return {"Success": True}
