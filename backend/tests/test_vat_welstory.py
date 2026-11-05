"""Mẫu bảng kê tách thuế GTGT (Welstory), kỳ chốt ngày 25, đơn vị bán hàng riêng, xóa khách / xóa phiếu."""
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.demo import welstory_t10 as W
from app.errors import BusinessError, PeriodLockedError
from app.exporters import bangke_excel as bx
from app.models import AssetMovement, DeliverySlip, DocStatus, InventoryTransaction, Partner, StatementStatus, VatMode
from app.money import round_money
from app.services import cashbook, debts, masterdata, sales, statements


@pytest.fixture()
def wel(session):
    return W.seed(session)


def test_period_cut_day_and_vat_totals(session, wel):
    st = statements.build_statement(session, wel["partner_id"], "2026-10")
    assert st["template"] == "VAT_DETAIL" and st["vat_mode"] == VatMode.EXCLUDED
    assert st["period_text"] == "Từ ngày 26/09/2026 đến hết ngày 25/10/2026"
    assert st["sheet_name"] == "T10.2026"
    g = st["grand_total"]
    assert g["net_kg"] == Decimal("6764.3")
    assert g["amount_before_tax"] == W.EXPECTED_BEFORE_TAX               # 232.074.420,9 – giữ số lẻ
    assert g["vat_amount"] == Decimal("18565953.672")
    # làm tròn lũy kế: tổng phiếu = làm tròn(tổng số lẻ) như Excel – không lệch đồng nào
    assert g["amount"] == round_money(W.EXPECTED_TOTAL_EXACT) == Decimal("250640375")
    assert st["period_words"].startswith("Hai trăm năm mươi triệu, sáu trăm bốn mươi ngàn, ba trăm bảy mươi lăm")
    assert st["seller"]["name"] == "CÔNG TY CỔ PHẦN METRO TOÀN CẦU"
    assert st["closing_loans_text"] == "Tổng mượn 26 vỏ PC45"
    # phiếu 27/09 thuộc kỳ T10 (chốt 25)
    slip = session.get(DeliverySlip, wel["slip_ids"][0])
    assert slip.period == "2026-10" and slip.vat_mode == VatMode.EXCLUDED
    assert debts.balance(session, wel["partner_id"]) == Decimal("250640375")


def test_export_matches_welstory_template(session, wel):
    data, name = bx.build_workbook_bytes(session, wel["partner_id"], "2026-10")
    assert name == "BangKe_WELSTORY_T10.2026.xlsx"
    ws = load_workbook(BytesIO(data))["T10.2026"]
    assert ws["A1"].value == "CÔNG TY CỔ PHẦN METRO TOÀN CẦU"
    assert ws["A6"].value.strip() == "BẢNG KÊ CHI TIẾT"
    assert ws["A8"].value.startswith("Đơn vị bán hàng : CÔNG TY CỔ PHẦN METRO TOÀN CẦU")
    heads = [ws.cell(15, c).value for c in range(1, 10)]
    assert heads == ["Ngày tháng", "Loại bình", "Gas dư", "Thực giao", "Đơn giá chưa thuế (đ/kg)",
                     "Thành tiền chưa thuế", "Tiền thuế GTGT 8%", "Tổng cộng thanh toán", "Ghi chú"]
    assert ws["B16"].value == "PC 45"
    # công thức y như file gốc
    assert ws["D18"].value == "=+B18*45-C18"
    assert ws["F18"].value == "=+D18*E18"
    assert ws["G18"].value == "=F18*8%"
    assert ws["H18"].value == "=F18+G18"
    assert ws["E20"].value == 34682 and ws["I23"].value == "Mượn 03 vỏ pc45 ( 2 van )"
    assert ws["A27"].value == "Tổng" and ws["H27"].value == "=SUM(H17:H26)"
    assert ws["D30"].value == "=H27"
    assert ws["B29"].value.startswith("=D")            # bằng chữ = khối công thức ẩn
    assert ws["H35"].value == "ĐẠI DIỆN BÊN BÁN"


def test_rebalance_after_delete_and_cancel(session, wel):
    pid = wel["partner_id"]
    sales.cancel_slip(session, wel["slip_ids"][1], "nhập nhầm")
    sales.delete_slip(session, wel["slip_ids"][2])
    st = statements.build_statement(session, pid, "2026-10")
    exact = sum((D_(s) for s in session.query(DeliverySlip).filter(
        DeliverySlip.partner_id == pid, DeliverySlip.status == DocStatus.POSTED)), Decimal(0))
    assert st["grand_total"]["amount"] == round_money(exact)
    assert session.get(DeliverySlip, wel["slip_ids"][2]) is None


def D_(s):
    return Decimal(s.amount_before_tax) + Decimal(s.vat_amount)


def test_delete_posted_slip_reverses_stock_and_assets(session, demo):
    sid = demo["slip_ids"][0]
    slip = session.get(DeliverySlip, sid)
    pid, total = slip.partner_id, slip.total
    before = debts.balance(session, pid)
    has_assets = session.query(AssetMovement).filter(AssetMovement.slip_id == sid).count()
    out = sales.delete_slip(session, sid, reason="trùng")
    assert out["was_posted"] and session.get(DeliverySlip, sid) is None
    assert debts.balance(session, pid) == before - total
    inv = session.query(InventoryTransaction).filter(InventoryTransaction.ref_id == sid,
                                                     InventoryTransaction.ref_type == "SLIP").all()
    assert sum(Decimal(t.qty_change) for t in inv) == 0      # xuất kho đã được đảo
    if has_assets:
        assert session.query(AssetMovement).filter(AssetMovement.slip_id == sid).count() == 0


def test_delete_slip_blocked_by_lock_and_receipt(session, demo):
    pid = demo["partner_id"]
    st = statements.save_statement(session, pid, "2026-10")
    statements.set_status(session, st.id, StatementStatus.CONFIRMED)
    with pytest.raises(PeriodLockedError):
        sales.delete_slip(session, demo["slip_ids"][0])
    statements.set_status(session, st.id, StatementStatus.SENT)
    res = sales.create_slip(session, sales.SlipInput(partner_id=pid, slip_date=date(2026, 10, 30),
                                                     items=[sales.ItemInput(sku="PC48", qty=1)], paid_amount=1000))
    with pytest.raises(BusinessError, match="phiếu thu"):
        sales.delete_slip(session, res.slip.id)


def test_delete_partner_or_deactivate(session, wel):
    # khách chưa có chứng từ: xóa hẳn (kèm bếp, bảng giá)
    p = masterdata.create_partner(session, code="TMP", name="Khách thử")
    masterdata.create_site(session, partner_id=p.id, name="Bếp thử", aliases=["bep thu 2"])
    session.commit()
    assert masterdata.delete_partner(session, p.id)["deleted"]
    session.commit()
    assert session.get(Partner, p.id) is None
    # khách đã có phiếu: chặn xóa, cho ngừng theo dõi
    with pytest.raises(BusinessError, match="không xóa được"):
        masterdata.delete_partner(session, wel["partner_id"])
    out = masterdata.delete_partner(session, wel["partner_id"], deactivate_if_used=True)
    session.commit()
    assert out["deactivated"] and out["usage"]["phiếu bán hàng"] == 8
    assert wel["partner_id"] not in [x.id for x in masterdata.list_partners(session)]
    assert wel["partner_id"] in [x.id for x in masterdata.list_partners(session, include_inactive=True)]


def test_api_delete_and_companies(engine, session, wel, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.main import app

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _s():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _s
    try:
        with TestClient(app) as c:
            comps = c.get("/api/companies").json()
            assert [x["short_name"] for x in comps][-1] == "Metro Toàn Cầu"
            r = c.delete(f"/api/partners/{wel['partner_id']}")
            assert r.status_code == 400 and "Ngừng theo dõi" in r.json()["detail"]
            u = c.get(f"/api/partners/{wel['partner_id']}/usage").json()
            assert not u["can_delete"]
            r = c.delete(f"/api/slips/{wel['slip_ids'][-1]}")
            assert r.status_code == 200, r.text
            assert c.get(f"/api/slips/{wel['slip_ids'][-1]}").status_code == 404
            # sửa khách: đổi cách tính thuế / mẫu / ngày chốt
            r = c.patch(f"/api/partners/{wel['partner_id']}", json={"statement_cut_day": 0})
            assert r.status_code == 200 and r.json()["statement_cut_day"] is None
            r = c.patch(f"/api/partners/{wel['partner_id']}", json={"statement_template": "XYZ"})
            assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
