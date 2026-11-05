"""Đăng nhập HTTP Basic (bật khi chạy trên VPS) – vai trò lấy từ tài khoản, không tin header trình duyệt."""
import base64
from datetime import date
from decimal import Decimal

from app import main as app_main
from app.auth import check_basic, hash_password, load_accounts, verify_password
from app.db import transaction
from app.services import masterdata

from .test_order_flow import _client


def _hdr(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_hash_and_verify():
    h = hash_password("Mật-khẩu-1")
    assert h.startswith("pbkdf2~") and verify_password("Mật-khẩu-1", h) and not verify_password("sai", h)
    acc = load_accounts(f"hien:{h}:CHIEF_ACCOUNTANT, lan:plain~123")
    assert acc["hien"].role == "CHIEF_ACCOUNTANT" and acc["lan"].role == "STAFF"
    assert check_basic(_hdr("lan", "123")["Authorization"], acc).username == "lan"
    assert check_basic(_hdr("lan", "x")["Authorization"], acc) is None


def test_basic_auth_middleware(engine, session, monkeypatch):
    monkeypatch.setattr(app_main, "_ACCOUNTS", load_accounts(
        f"hien:{hash_password('kt')}:CHIEF_ACCOUNTANT,lan:plain~nv:STAFF"))
    with transaction(session):
        cust = masterdata.create_partner(session, code="K1", name="Khách 1", credit_limit=Decimal(1000))
        masterdata.create_product(session, sku="PC12", name="Gas 12kg", capacity_kg="12", sale_price="400000")
    c = _client(engine)
    assert c.get("/api/health").status_code == 200
    r = c.get("/api/orders")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert c.get("/api/orders", headers=_hdr("lan", "sai")).status_code == 401
    assert c.get("/api/me", headers=_hdr("lan", "nv")).json() == {"user": "lan", "role": "STAFF", "auth_enabled": True}
    body = {"partner_id": cust.id, "order_date": str(date(2026, 11, 2)), "submit": True,
            "items": [{"sku": "PC12", "ordered_qty": "1"}]}
    order = c.post("/api/orders", json=body, headers=_hdr("lan", "nv")).json()
    assert order["status"] == "PENDING_APPROVAL" and order["created_by"] == "lan"
    # nhân viên giả header X-Role: MANAGER vẫn bị chặn
    r = c.post(f"/api/orders/{order['id']}/approve", json={}, headers={**_hdr("lan", "nv"), "X-Role": "MANAGER"})
    assert r.status_code == 403
    r = c.post(f"/api/orders/{order['id']}/approve", json={}, headers=_hdr("hien", "kt"))
    assert r.status_code == 200 and r.json()["approved_by"] == "hien"
    app_main.app.dependency_overrides.clear()
