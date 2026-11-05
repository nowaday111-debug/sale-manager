from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.main import app


def _client(engine):
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_full_flow_over_http(engine):
    c = _client(engine)
    assert c.put("/api/company", json={"name": "CTY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI",
                                       "tax_code": "0107776927"}).status_code == 200
    r = c.post("/api/partners", json={"code": "KF", "name": "CÔNG TY TNHH KOREA FOOD VIỆT NAM",
                                      "tax_code": "109477792"})
    assert r.status_code == 201 and r.json()["tax_code"] == "0109477792"
    partner_id = r.json()["id"]
    site = c.post(f"/api/partners/{partner_id}/sites", json={"name": "Bếp Gầu bò CT4 - Mỹ Đình",
                                                            "aliases": ["Gầu bò"]}).json()
    prod = c.post("/api/products", json={"sku": "PC45", "name": "Gas 45kg", "capacity_kg": "45"}).json()
    c.post("/api/prices", json={"product_id": prod["id"], "partner_id": partner_id, "price": "49000",
                                "valid_from": "2026-01-01"})
    body = {"partner_id": partner_id, "site_id": site["id"], "slip_date": "2026-10-03", "slip_no": "0018100",
            "items": [{"sku": "PC45", "qty": "4"}], "gas_residual_kg": "7",
            "assets": [{"asset_type": "EMPTY_CYLINDER", "direction": "LEND", "qty": "1", "sku": "PC45"}]}
    r = c.post("/api/slips", json=body)
    assert r.status_code == 201, r.text
    slip = r.json()["slip"]
    assert slip["total"] == "8477000"          # (4*45 - 7) * 49.000 – khớp file Gầu bò T10 dòng 18
    assert isinstance(slip["total"], str)       # tiền trả về dạng chuỗi, không bị float hóa
    dup = c.post("/api/slips", json=body)
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate"
    found = c.get("/api/sites/find", params={"text": "gau bo", "partner_id": partner_id}).json()
    assert found["id"] == site["id"]
    c.post("/api/cash/vouchers", json={"voucher_type": "RECEIPT", "voucher_date": "2026-10-20", "amount": "8000000",
                                       "partner_id": partner_id})
    st = c.get(f"/api/statements/preview/{partner_id}/2026-10").json()
    assert st["closing_debt"] == "477000"
    assert st["amount_in_words"] == "Bốn trăm bảy mươi bảy ngàn đồng chẵn."
    assert st["blocks"][0]["site_label"] == "Bếp Gầu bò CT4 - Mỹ Đình\nMượn 01 vỏ PC45 - ko cược"
    saved = c.post("/api/statements", json={"partner_id": partner_id, "period": "2026-10"}).json()
    assert c.post(f"/api/statements/{saved['id']}/status", json={"status": "CONFIRMED"}).status_code == 200
    locked = c.post("/api/slips", json={**body, "slip_no": "0018101"})
    assert locked.status_code == 423 and locked.json()["code"] == "period_locked"
    assert c.get("/api/reports/dashboard", params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).json()[
        "revenue"] == "8477000"
    app.dependency_overrides.clear()
