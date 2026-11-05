"""Bước 4 – xuất Bảng kê ra Excel đúng mẫu."""
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.errors import BusinessError
from app.exporters import bangke_excel as bx
from app.services import masterdata


def _find(ws, text):
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.strip() == text:
                return c
    raise AssertionError(f"không thấy ô '{text}'")


def _wb(session, demo):
    data, name = bx.build_workbook_bytes(session, demo["partner_id"], "2026-10")
    return load_workbook(BytesIO(data)), name


def test_layout_matches_template(session, demo):
    wb, name = _wb(session, demo)
    assert name == "BangKe_STG_T10.2026.xlsx"
    ws = wb["T10.2026"]
    assert ws["B7"].value == "BẢNG KÊ CHI TIẾT"
    assert ws["B8"].value == "Từ ngày 01/10/2026 đến hết ngày 31/10/2026"
    assert ws["D12"].value == "CÔNG TY CỔ PHẦN TẬP ĐOÀN STARGATE"
    assert ws["D14"].value == "0101358112"   # MST giữ dạng chữ, không mất số 0
    assert ws["B16"].value == "Ngày tháng" and ws["D17"].value == "PC48"
    assert ws["B16"].font.name == "Times New Roman" and ws["B16"].font.b
    assert "B7:J7" in {str(r) for r in ws.merged_cells.ranges}
    assert int(ws.page_setup.paperSize) == 9 and ws.page_setup.orientation == "portrait"
    assert ws.print_title_rows.replace("$", "") == "16:17"


def test_formulas_like_original(session, demo):
    wb, _ = _wb(session, demo)
    ws = wb.active
    # dòng phiếu đầu tiên khối Thường Tín: =D*48-E ; =F*G (bố cục 1 loại bình: D PC48, E gas dư, F thực giao...)
    row = next(c.row for c in ws["C"] if c.value == 4638)
    assert ws[f"F{row}"].value == f"=D{row}*48-E{row}"
    assert ws[f"H{row}"].value == f"=F{row}*G{row}"
    assert ws[f"E{row}"].value == 42.4
    total = _find(ws, "Tổng cộng")
    assert ws.cell(total.row, 8).value.startswith("=+H")
    closing = _find(ws, "Công nợ còn lại phải thanh toán là:")
    assert ws.cell(closing.row, 8).value.startswith("=+H")
    words = _find(ws, "Bằng chữ:")
    assert ws.cell(words.row, 4).value.startswith("=D")         # trỏ tới khối công thức đọc số như file gốc
    assert ws.cell(total.row, 9).value == "Tổng mượn 184 vỏ PC48 + 13 bếp CN"


def test_values_add_up(session, demo):
    """Cộng lại các ô giá trị trong file = đúng số phần mềm tính (không cần mở Excel)."""
    wb, _ = _wb(session, demo)
    ws = wb.active
    prior = [ws.cell(r, 8).value for r in range(1, ws.max_row + 1)
             if isinstance(ws.cell(r, 2).value, str) and "chưa thanh toán" in ws.cell(r, 2).value]
    paid = [ws.cell(r, 8).value for r in range(1, ws.max_row + 1)
            if isinstance(ws.cell(r, 2).value, str) and ws.cell(r, 2).value.endswith("thanh toán")
            and ws.cell(r, 2).value.startswith("Ngày")]
    assert sum(prior) == 224283700 + 675678850 + 783130800
    assert sum(paid) == 1049962550


def test_export_into_customer_workbook(session, demo, tmp_path: Path):
    path = tmp_path / "STG.xlsx"
    wb = Workbook()
    wb.active.title = "T9.2026"
    wb.active["A1"] = "giữ nguyên"
    wb.create_sheet("Ghi chú")
    wb.save(path)
    masterdata.update_partner(session, demo["partner_id"], workbook_path=str(path))

    res = bx.export_to_workbook(session, demo["partner_id"], "2026-10")
    assert res["sheet"] == "T10.2026" and Path(res["backup"]).is_file()
    wb2 = load_workbook(path)
    assert wb2.sheetnames == ["T9.2026", "Ghi chú", "T10.2026"]
    assert wb2["T9.2026"]["A1"].value == "giữ nguyên"
    assert wb2["T9.2026"].sheet_state == "hidden"          # ẩn tháng cũ
    assert wb2["Ghi chú"].sheet_state == "visible"         # sheet khác giữ nguyên
    assert wb2.active.title == "T10.2026"

    with pytest.raises(BusinessError):                      # không ghi đè khi chưa cho phép
        bx.export_to_workbook(session, demo["partner_id"], "2026-10")
    res = bx.export_to_workbook(session, demo["partner_id"], "2026-10", replace=True)
    assert load_workbook(path).sheetnames == ["T9.2026", "Ghi chú", "T10.2026"]


def test_export_api(session, demo, engine):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.main import app

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as client:
            r = client.get(f"/api/statements/export/{demo['partner_id']}/2026-10")
            assert r.status_code == 200
            assert "spreadsheetml" in r.headers["content-type"]
            assert load_workbook(BytesIO(r.content)).sheetnames == ["T10.2026"]
    finally:
        app.dependency_overrides.clear()


def test_backup_sqlite(tmp_path, monkeypatch):
    import sqlite3

    from app import backup
    db = tmp_path / "sale.db"
    with sqlite3.connect(db) as c:
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")
    monkeypatch.setattr(backup, "sqlite_path", lambda url=None: db)
    out = backup.backup_now(force=True)
    assert out.is_file() and out.parent.name == "backups"
    with sqlite3.connect(out) as c:
        assert c.execute("select x from t").fetchone() == (1,)


def test_frontend_served():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200 and "Sale-manager" in r.text
        assert client.get("/app.js").status_code == 200
        assert client.get("/vendor/react.production.min.js").status_code == 200
