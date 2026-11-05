"""Xuất Bảng kê tháng ra Excel.

Chạy trong thư mục backend:
    python -m scripts.export_bangke --partner STG --period 2026-10                  # thêm sheet T10.2026 vào workbook của khách
    python -m scripts.export_bangke --partner STG --period 2026-10 --file D:/BangKe/STG.xlsx
    python -m scripts.export_bangke --partner STG --period 2026-10 --rieng           # file riêng trong data/exports/
    python -m scripts.export_bangke --demo                                           # DB demo Stargate -> data/exports/
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import engine as default_engine
from app.db import init_db, make_engine
from app.exporters import bangke_excel
from app.models import Partner


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner", help="mã khách hàng (partners.code)")
    ap.add_argument("--period", help="kỳ YYYY-MM")
    ap.add_argument("--file", help="workbook muốn thêm sheet (mặc định: partners.workbook_path)")
    ap.add_argument("--rieng", action="store_true", help="xuất file riêng chỉ có sheet tháng này")
    ap.add_argument("--ghi-de", action="store_true", help="ghi đè sheet cùng tên nếu đã có")
    ap.add_argument("--khong-an", action="store_true", help="không ẩn các sheet tháng cũ")
    ap.add_argument("--db", help="đường dẫn file SQLite")
    ap.add_argument("--demo", action="store_true", help="dùng dữ liệu demo Stargate T10/2026 (DB tạm trong bộ nhớ)")
    args = ap.parse_args()

    if args.demo:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from app.demo import stargate_t10
        engine = create_engine("sqlite://", poolclass=StaticPool)
        init_db(engine)
        session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
        ids = stargate_t10.seed(session)
        partner_id, period = ids["partner_id"], "2026-10"
        args.rieng = True
    else:
        if not args.partner or not args.period:
            ap.error("cần --partner và --period (hoặc --demo)")
        engine = make_engine(f"sqlite:///{Path(args.db).resolve().as_posix()}") if args.db else default_engine
        init_db(engine)
        session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
        partner = session.scalar(select(Partner).where(Partner.code == args.partner))
        if partner is None:
            ap.error(f"không có khách mã {args.partner}")
        partner_id, period = partner.id, args.period

    if args.rieng:
        data, name = bangke_excel.build_workbook_bytes(session, partner_id, period)
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        out = settings.export_dir / name
        out.write_bytes(data)
        print(f"Đã xuất {out}")
        return
    res = bangke_excel.export_to_workbook(session, partner_id, period, workbook_path=args.file,
                                          replace=args.ghi_de, hide_old_months=not args.khong_an)
    print(f"Đã thêm sheet {res['sheet']} vào {res['path']}")
    if res["backup"]:
        print(f"Bản sao lưu: {res['backup']}")
    print(f"Còn phải thanh toán: {res['closing_debt']:,.0f} đ".replace(",", "."))


if __name__ == "__main__":
    main()
