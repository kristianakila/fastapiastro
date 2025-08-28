import os
import secrets
import requests
import hashlib
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Настройка CORS — разрешаем запросы с вашего фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://astf.vercel.app"],  # Только ваш домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    # Формируем строку для хэширования: все поля + Password, отсортированные по ключу
    data_with_password = {**data, "Password": SECRET_KEY}
    token_string = ''.join(str(v) for _, v in sorted(data_with_password.items()))
    return hashlib.sha256(token_string.encode('utf-8')).hexdigest()

@app.post("/init-payment")
def init_payment(payload: PaymentRequest):
    data = {
        "TerminalKey": TERMINAL_KEY,
        "Amount": payload.amount,
        "OrderId": payload.orderId,
        "Description": payload.description,
        "CustomerEmail": payload.email,
    }
    # Генерируем токен
    data["Token"] = generate_token(data)

    try:
        # ⚠️ ВАЖНО: убрал лишние пробелы в URL!
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

    # Убираем Token перед расчётом ожидаемого хеша
    payload_copy = payload.copy()
    payload_copy.pop("Token", None)

    expected_token = generate_token(payload_copy)

    # Защищённое сравнение (временная атака)
    if not secrets.compare_digest(received_token, expected_token):
        return {"Success": False, "error": "Invalid token"}

    # Теперь доверяем данным
    status = payload.get("Status")
    order_id = payload.get("OrderId")

    # 🚀 Здесь можно добавить логику:
    # - сохранить в базу
    # - выдать доступ
    # - отправить письмо и т.д.
    #
    # Пример:
    # if status == "CONFIRMED":
    #     await activate_subscription(order_id)

    return {"Success": True}
