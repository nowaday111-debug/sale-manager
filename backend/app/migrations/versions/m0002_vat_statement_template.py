"""0002 – Thuế GTGT tách riêng + mẫu bảng kê theo khách + nhiều đơn vị bán hàng.

* partners: + vat_mode (INCLUDED | EXCLUDED), statement_template (STANDARD | VAT_DETAIL),
  statement_cut_day (VD 25: kỳ từ 26 tháng trước đến 25), seller_company_id (đơn vị bán in trên bảng kê)
* delivery_slips: + vat_mode, vat_rate, amount_before_tax, vat_amount
* company_profile: + short_name (để chọn khi có nhiều đơn vị bán hàng, VD Cty CP Metro Toàn Cầu)
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from .. import add_column


def upgrade(conn: Connection) -> None:
    money = "VARCHAR(40)" if conn.dialect.name == "sqlite" else "NUMERIC(20, 4)"
    add_column(conn, "partners", "vat_mode", "VARCHAR(10) NOT NULL", "'INCLUDED'")
    add_column(conn, "partners", "statement_template", "VARCHAR(20) NOT NULL", "'STANDARD'")
    add_column(conn, "partners", "statement_cut_day", "INTEGER")
    add_column(conn, "partners", "seller_company_id", "INTEGER REFERENCES company_profile(id) ON DELETE SET NULL")
    add_column(conn, "delivery_slips", "vat_mode", "VARCHAR(10) NOT NULL", "'INCLUDED'")
    add_column(conn, "delivery_slips", "vat_rate", money)
    add_column(conn, "delivery_slips", "amount_before_tax", money)
    add_column(conn, "delivery_slips", "vat_amount", money)
    add_column(conn, "company_profile", "short_name", "VARCHAR(100)")
