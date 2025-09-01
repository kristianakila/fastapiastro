import os
import secrets
import requests
import hashlib
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import threading
import time

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# Инициализация Firestore
firebase_key_json = os.getenv("FIREBASE_KEY_JSON")
if not firebase_key_json:
    raise RuntimeError("FIREBASE_KEY_JSON is not set")

cred = credentials.Certificate(json.loads(firebase_key_json))
firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://astf.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TERMINAL_KEY = os.getenv("TERMINAL_KEY", "1691507148627DEMO")
SECRET_KEY = os.getenv("SECRET_KEY", "bm5fjkoz0s5vw87j")
YOUR_SERVER_URL = os.getenv("SERVER_URL", "https://fastapiastro.onrender.com")

# ----------------- Models -----------------
class PaymentRequest(BaseModel):
    orderId: str
    amount: int
    description: str
    email: str
    customerKey: str
    productType: str  # subscription | one-time

class ChargeRequest(BaseModel):
    amount: int
    rebillId: str
    customerKey: str

# ----------------- Token generation -----------------
def generate_token(data: dict) -> str:
    data_with_password = {**data, "Password": SECRET_KEY}
    token_string = ''.join(str(v) for _, v in sorted(data_with_password.items()))
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()

# ----------------- Telegram -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_IDS = os.getenv("TELEGRAM_ADMIN_IDS", "")
ADMIN_IDS = [admin.strip() for admin in TELEGRAM_ADMIN_IDS.split(",") if admin.strip()]

def send_telegram_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не задан")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        r.raise_for_status()
        print(f"📩 Сообщение отправлено {chat_id}")
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)

# ----------------- Subscription check -----------------
def check_and_update_subscription(doc_ref, user_data):
    expires_at = user_data.get("subscription", {}).get("expiresAt")
    update_data = {}
    if expires_at:
        if isinstance(expires_at, firestore.firestore.SERVER_TIMESTAMP.__class__):
            return
        if expires_at.replace(tzinfo=None) < datetime.utcnow():
            update_data["subscription.status"] = "expired"
        else:
            update_data["subscription.status"] = "Premium"
    if update_data:
        update_data["subscription.checkedAt"] = firestore.SERVER_TIMESTAMP
        doc_ref.update(update_data)

def periodic_subscription_check():
    while True:
        try:
            users_ref = db.collection("telegramUsers").stream()
            for doc in users_ref:
                user_data = doc.to_dict()
                check_and_update_subscription(db.collection("telegramUsers").document(doc.id), user_data)
        except Exception as e:
            print("Ошибка проверки подписок:", e)
        time.sleep(3600)

threading.Thread(target=periodic_subscription_check, daemon=True).start()

# ----------------- Init payment -----------------
@app.post("/init-payment")
async def init_payment(payment_request: PaymentRequest):
    try:
        description = f"{payment_request.description} | {payment_request.productType}"

        init_payload = {
            "TerminalKey": TERMINAL_KEY,
            "Amount": payment_request.amount,
            "OrderId": payment_request.orderId,
            "Description": description,
            "CustomerKey": payment_request.customerKey,
        }
        init_payload["Token"] = generate_token(init_payload)

        response = requests.post("https://securepay.tinkoff.ru/v2/Init", json=init_payload)
        resp_data = response.json()

        if resp_data.get("Success"):
            # Сохраняем только PaymentId, PaymentURL, orderId и productType
            db.collection("telegramUsers").document(payment_request.customerKey).set({
                "orderId": payment_request.orderId,
                "productType": payment_request.productType,
                "tinkoff": {
                    "PaymentId": resp_data["PaymentId"],
                    "PaymentURL": resp_data["PaymentURL"]
                }
            }, merge=True)

            return {"PaymentURL": resp_data["PaymentURL"], "PaymentId": resp_data["PaymentId"]}
        else:
            raise HTTPException(status_code=400, detail=resp_data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------- Tinkoff callback -----------------
@app.post("/tinkoff-callback")
async def tinkoff_callback(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    print("🔥 Callback POST получен:", payload)

    if not payload:
        return {"Success": False, "error": "Empty payload"}

    received_token = payload.get("Token")
    customer_key = payload.get("CustomerKey")
    if not received_token or not customer_key:
        return {"Success": False, "error": "Missing Token or CustomerKey"}

    payload_copy = dict(payload)
    payload_copy.pop("Token", None)
    expected_token = generate_token(payload_copy)

    if not secrets.compare_digest(received_token, expected_token):
        return {"Success": False, "error": "Invalid token"}

    status = payload.get("Status")
    # Получаем продукт для правильного обновления
    user_ref = db.collection("telegramUsers").document(customer_key)
    user_doc = user_ref.get()
    if not user_doc.exists:
        return {"Success": False, "error": "User not found"}
    user_data = user_doc.to_dict()
    product_type = user_data.get("productType", "subscription")

    update_data = {"tinkoff.lastCallbackPayload": payload, "tinkoff.updatedAt": firestore.SERVER_TIMESTAMP}

    if status and status.lower() == "confirmed":
        if product_type == "subscription":
            expire_at = datetime.utcnow() + timedelta(days=30)
            update_data.update({
                "subscription.status": "Premium",
                "subscription.expiresAt": expire_at
            })
        else:  # one-time
            # Можно добавлять баланс или флаг использования
            update_data.update({
                "balance": user_data.get("balance", 0) + 1
            })
        user_ref.update(update_data)
    else:
        # при failed или pending можно ничего не менять
        user_ref.update(update_data)

    return {"Success": True}

# ----------------- Root -----------------
@app.get("/")
def root():
    return {"status": "ok"}

