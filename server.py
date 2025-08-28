import os
import secrets
import requests
import hashlib
import json
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
    customerKey: str

class ChargeRequest(BaseModel):
    amount: int
    rebillId: str
    customerKey: str

def generate_token(data: dict) -> str:
    data_with_password = {**data, "Password": SECRET_KEY}
    token_string = ''.join(str(v) for _, v in sorted(data_with_password.items()))
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()

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

# 3️⃣ Callback от Tinkoff с логированием и GET для тестов
@app.post("/tinkoff-callback")
@app.get("/tinkoff-callback")
async def tinkoff_callback(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    print(f"🔥 Callback {request.method} получен:", payload)

    if request.method != "POST" or not payload:
        return {"Success": False, "info": "Callback endpoint, ожидается POST с JSON"}

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
            "subscription.status": (status or "").lower(),
            "subscription.updatedAt": firestore.SERVER_TIMESTAMP,
            "subscription.lastCallbackPayload": payload
        }
        if rebill_id:
            update_data["tinkoff.RebillId"] = rebill_id
        db.collection("telegramUsers").document(customer_key).update(update_data)

    return {"Success": True}
