import os
from fastapi import FastAPI
from pydantic import BaseModel
import requests
import hashlib

app = FastAPI()

TERMINAL_KEY = os.getenv("TERMINAL_KEY", "1691507148627DEMO")
SECRET_KEY = os.getenv("SECRET_KEY")

class PaymentRequest(BaseModel):
    orderId: str
    amount: int
    description: str
    email: str

def generate_token(data: dict) -> str:
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
    r = requests.post("https://securepay.tinkoff.ru/v2/Init", json=data)
    return r.json()