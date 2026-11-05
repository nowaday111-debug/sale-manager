"""0001 – Quy trình Đơn đặt hàng -> Biên bản giao nhận -> Sổ cái & Đối chiếu công nợ.

* partners: + email, credit_limit (đã có), payment_term_days, current_debt, current_payable,
  representative, representative_title
* cash_vouchers: + is_locked (khóa bởi BBĐCCN RECONCILED)
* Bảng mới (tạo bởi create_all trước khi migration chạy): orders, order_items, delivery_notes,
  delivery_note_items, debt_ledger_entries, debt_reconciliations, debt_reconciliation_lines
* Index đối chiếu cuối tháng: (partner_id, transaction_date), order_id, delivery_note_id, status

Tiền kiểu DecimalType: SQLite lưu VARCHAR(40) (chính xác tuyệt đối), PostgreSQL NUMERIC(20,4).
"""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from .. import add_column, create_index


def _money(conn: Connection) -> str:
    return "VARCHAR(40)" if conn.dialect.name == "sqlite" else "NUMERIC(20, 4)"


def upgrade(conn: Connection) -> None:
    money = _money(conn)
    false = "0" if conn.dialect.name == "sqlite" else "FALSE"
    # ---- partners
    add_column(conn, "partners", "email", "VARCHAR(150)")
    add_column(conn, "partners", "credit_limit", money)
    add_column(conn, "partners", "payment_term_days", "INTEGER NOT NULL", "30")
    add_column(conn, "partners", "current_debt", f"{money} NOT NULL", "'0'" if conn.dialect.name == "sqlite" else "0")
    add_column(conn, "partners", "current_payable", f"{money} NOT NULL",
               "'0'" if conn.dialect.name == "sqlite" else "0")
    add_column(conn, "partners", "representative", "VARCHAR(150)")
    add_column(conn, "partners", "representative_title", "VARCHAR(100)")
    # ---- cash_vouchers
    add_column(conn, "cash_vouchers", "is_locked", "BOOLEAN NOT NULL", false)

    # ---- index (create_all đã tạo khi bảng mới; giữ ở đây cho DB đã có bảng từ bản thử nghiệm)
    tables = set(inspect(conn).get_table_names())
    wanted = [
        ("ix_orders_partner_date", "orders", ["partner_id", "order_date"]),
        ("ix_orders_status", "orders", ["status"]),
        ("ix_order_items_order_id", "order_items", ["order_id"]),
        ("ix_dn_partner_date", "delivery_notes", ["partner_id", "delivery_date"]),
        ("ix_dn_status", "delivery_notes", ["status"]),
        ("ix_delivery_notes_order_id", "delivery_notes", ["order_id"]),
        ("ix_delivery_note_items_delivery_note_id", "delivery_note_items", ["delivery_note_id"]),
        ("ix_delivery_note_items_order_item_id", "delivery_note_items", ["order_item_id"]),
        ("ix_ledger_partner_date", "debt_ledger_entries", ["partner_id", "transaction_date"]),
        ("ix_ledger_order", "debt_ledger_entries", ["order_id"]),
        ("ix_ledger_delivery_note", "debt_ledger_entries", ["delivery_note_id"]),
        ("ix_ledger_reconciliation", "debt_ledger_entries", ["reconciliation_id"]),
        ("ix_ledger_ref", "debt_ledger_entries", ["reference_type", "reference_id"]),
        ("ix_recon_partner_period", "debt_reconciliations", ["partner_id", "period_from", "period_to"]),
        ("ix_recon_status", "debt_reconciliations", ["status"]),
        ("ix_debt_reconciliation_lines_reconciliation_id", "debt_reconciliation_lines", ["reconciliation_id"]),
        ("ix_debt_reconciliation_lines_ledger_entry_id", "debt_reconciliation_lines", ["ledger_entry_id"]),
    ]
    for name, table, cols in wanted:
        if table in tables:
            create_index(conn, name, table, cols)
