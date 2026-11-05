"""Nhập lịch sử từ các workbook Bảng kê Excel cũ vào database và tự đối chiếu với số trên file.

Nguyên tắc:
- Mỗi workbook = 1 khách hàng; mỗi khối trong sheet = 1 bếp; mỗi dòng có ngày = 1 phiếu giao (nguồn EXCEL).
- Không trừ kho với dữ liệu lịch sử (tồn kho thật khai báo bằng kiểm kê sau khi nhập).
- Công nợ: chọn "tháng chuyển đổi" (sheet đầu tiên có dòng "Công nợ còn lại phải thanh toán").
  Nợ các tháng trước = đúng số còn nợ ghi trên sheet đó (điều chỉnh bằng số dư chuyển đổi); từ tháng chuyển đổi trở đi
  nhập từng lần thanh toán. File không có phần công nợ -> giả định đã thanh toán đủ các tháng trước tháng mới nhất.
- Vỏ bình đang mượn của mỗi bếp được chỉnh cho khớp dòng "Mượn N vỏ ..." trên bảng kê cuối mỗi tháng.
- Nhập lại cùng file nhiều lần không bị trùng (bỏ qua phiếu / thanh toán đã có).
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import transaction
from ..errors import BusinessError
from ..models import (
    AssetDirection,
    AssetType,
    CashVoucher,
    DebtSide,
    DeliverySlip,
    DocStatus,
    FundType,
    OpeningBalance,
    Partner,
    PartnerType,
    PriceBasis,
    Product,
    ProductKind,
    SlipType,
    SourceType,
    VoucherType,
)
from ..money import ZERO, D, round_money
from ..services import assets as asset_svc
from ..services import cashbook, debts, masterdata, sales, statements
from ..services.masterdata import normalize_text
from .bangke_parser import Block, ParsedSheet, SlipRow, parse_workbook

SERVICE_SKU = "DVKHAC"
RESIDUAL_SKU = "GASDU"
_VO = r"vo\s*(pc\s*\d+|mini)"


@dataclass
class ImportReport:
    file: str
    partner_id: int | None = None
    partner_name: str | None = None
    sheets: int = 0
    slips_created: int = 0
    slips_skipped_duplicate: int = 0
    vouchers_created: int = 0
    vouchers_skipped_duplicate: int = 0
    sites_created: list[str] = field(default_factory=list)
    cutover_period: str | None = None
    assumptions: list[str] = field(default_factory=list)
    sheet_checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def mismatches(self) -> list[dict]:
        return [c for c in self.sheet_checks if not c["ok"]]


# ---------------------------------------------------------------- phân tích chữ về vỏ bình / đầu tư
def parse_asset_text(text: str) -> list[dict]:
    """'Thu 6 vỏ pc45 và mượn 6 vỏ pc48' -> [RETURN 6 PC45, LEND 6 PC48]; '13 bếp CN' (mượn) -> STOVE."""
    t = normalize_text(text)
    out = []
    for verb, qty, sku in re.findall(r"(cho muon|muon|thu ve|thu hoi|thu|tra lai|tra)\s+(\d+)\s*" + _VO, t):
        sku = sku.replace(" ", "").upper()
        direction = AssetDirection.LEND if "muon" in verb else AssetDirection.RETURN
        if int(qty) > 0:
            out.append({"asset_type": AssetType.EMPTY_CYLINDER, "direction": direction, "qty": D(qty), "sku": sku})
    for verb, qty in re.findall(r"(muon|thu|tra)\s+(\d+)\s*bep\s*(?:gas\s*)?cn", t):
        if int(qty) == 0:
            continue
        out.append({"asset_type": AssetType.STOVE,
                    "direction": AssetDirection.LEND if verb == "muon" else AssetDirection.RETURN,
                    "qty": D(qty), "sku": None})
    return out


def parse_loan_total(text: str | None) -> dict[tuple[str, str], Decimal]:
    """'Mượn 30 vỏ pc48- ko cược + 13 bếp CN' -> {(EMPTY_CYLINDER,'PC48'):30, (STOVE,'bếp CN'):13}"""
    if not text:
        return {}
    t = normalize_text(text)
    out: dict[tuple[str, str], Decimal] = {}
    for qty, sku in re.findall(r"(\d+)\s*" + _VO, t):
        key = (AssetType.EMPTY_CYLINDER, sku.replace(" ", "").upper())
        out[key] = out.get(key, ZERO) + D(qty)
    for qty in re.findall(r"(\d+)\s*bep\s*(?:gas\s*)?cn", t):
        key = (AssetType.STOVE, asset_svc.ASSET_LABELS[AssetType.STOVE])
        out[key] = out.get(key, ZERO) + D(qty)
    return out


def _classify_note(text: str) -> tuple[str, str]:
    t = normalize_text(text)
    if t.startswith("tang") or " tang " in f" {t} ":
        return AssetType.GIFT, AssetDirection.GIFT
    if "cuu hoa" in t or "chua chay" in t or "tieu lenh" in t:
        return AssetType.EXTINGUISHER, AssetDirection.INVEST
    if "che" in t.split() or "khoa" in t or "van" in t or "sung moi" in t:
        return AssetType.VALVE, AssetDirection.INVEST
    if "bep" in t and "cn" in t:
        return AssetType.STOVE, AssetDirection.INVEST
    if "he thong" in t or "dan gas" in t:
        return AssetType.PIPING, AssetDirection.INVEST
    return AssetType.OTHER, AssetDirection.INVEST


# ---------------------------------------------------------------- danh mục
def _ensure_products(session: Session) -> dict[str, Product]:
    wanted = [
        ("PC45", "Gas Petrolimex bình 45kg", ProductKind.GAS_CYLINDER, 45, "bình", PriceBasis.KG),
        ("PC48", "Gas Petrolimex bình 48kg", ProductKind.GAS_CYLINDER, 48, "bình", PriceBasis.KG),
        ("PC12", "Gas Petrolimex bình 12kg", ProductKind.GAS_CYLINDER, 12, "bình", PriceBasis.KG),
        ("MINI", "Gas bình mini", ProductKind.GAS_CYLINDER, 12, "bình", PriceBasis.KG),
        (SERVICE_SKU, "Vật tư / dịch vụ khác", ProductKind.SERVICE, None, "lần", PriceBasis.UNIT),
        (RESIDUAL_SKU, "Thu hồi gas dư (trừ tiền)", ProductKind.SERVICE, None, "kg", PriceBasis.UNIT),
    ]
    out = {}
    for sku, name, kind, cap, unit, basis in wanted:
        p = session.scalar(select(Product).where(Product.sku == sku))
        if p is None:
            p = masterdata.create_product(session, sku=sku, name=name, kind=kind, capacity_kg=cap, unit=unit,
                                          default_price_basis=basis)
        out[sku] = p
    return out


def _partner_code(session: Session, name: str) -> str:
    words = [w for w in normalize_text(name).split() if w not in {"cong", "ty", "tnhh", "co", "phan", "cp"}]
    base = "".join(w[0] for w in words).upper()[:8] or "KH"
    code, i = base, 1
    while session.scalar(select(Partner.id).where(Partner.code == code)):
        i += 1
        code = f"{base}{i}"
    return code


def _ensure_partner(session: Session, sheets: list[ParsedSheet], partner_code: str | None, report: ImportReport,
                    workbook_path: str) -> Partner:
    latest = sheets[-1].buyer
    name = (latest.get("name") or "").strip() or Path(workbook_path).stem
    partner = None
    if partner_code:
        partner = session.scalar(select(Partner).where(Partner.code == partner_code.upper()))
    if partner is None and latest.get("tax_code"):
        partner = masterdata.find_partner_by_tax_code(session, latest["tax_code"])
    if partner is None:
        key = normalize_text(name)
        partner = next((p for p in session.scalars(select(Partner)) if normalize_text(p.name) == key), None)
    if partner is None:
        partner = masterdata.create_partner(
            session, code=partner_code or _partner_code(session, name), name=name, partner_type=PartnerType.CUSTOMER,
            tax_code=latest.get("tax_code"), address=latest.get("address"), phone=latest.get("phone"),
            workbook_path=workbook_path)
        old_names = sorted({s.buyer.get("name", "").strip() for s in sheets if s.buyer.get("name")} - {name})
        if old_names:
            partner.note = "Tên cũ trên bảng kê: " + "; ".join(old_names)
    elif not partner.workbook_path:
        partner.workbook_path = workbook_path
    return partner


def _site_names(sheets: list[ParsedSheet], partner: Partner) -> dict[int, str]:
    """Đặt tên bếp cho khối không ghi tên: tra theo dòng diễn giải đầu tư giống nhau ở sheet khác."""
    by_note: dict[str, str] = {}
    for ps in sheets:
        for b in ps.blocks:
            if b.site_name and b.header_note:
                by_note[normalize_text(b.header_note)[:40]] = b.site_name
    names = {}
    single = all(len(ps.blocks) <= 1 for ps in sheets) and not any(b.site_name for ps in sheets for b in ps.blocks)
    for ps in sheets:
        for b in ps.blocks:
            if b.site_name:
                names[id(b)] = b.site_name
            elif single:
                names[id(b)] = partner.name
            elif b.header_note and normalize_text(b.header_note)[:40] in by_note:
                names[id(b)] = by_note[normalize_text(b.header_note)[:40]]
            else:
                names[id(b)] = "Bếp chưa rõ tên"
    return names


# ---------------------------------------------------------------- dòng phiếu -> SlipInput
def _kg_price(row: SlipRow, sku: str, ps: ParsedSheet) -> tuple[Decimal, str]:
    """Đơn giá theo kg hay theo bình, đọc từ công thức cột Thành tiền của chính dòng đó."""
    price = row.unit_price or ZERO
    formula = (row.amount_formula or "").upper()
    if formula:
        letters = [k for k, v in ps.columns.get("sku_cols", {}).items() if v == sku]
        if any(re.search(rf"\b{col}{row.row}\b", formula) for col in letters):
            return price, PriceBasis.UNIT                    # "=C19*F19": số bình × đơn giá
        m = re.search(r"/\s*(\d+(?:\.\d+)?)\s*\*", formula)
        if m:
            return price / D(m.group(1)), PriceBasis.KG      # "=E19/45*F19": giá/bình 45kg -> giá/kg
        return price, PriceBasis.KG                          # "=G19*H19": thực giao (kg) × giá/kg
    if sku in ps.unit_skus:
        return price, PriceBasis.UNIT
    return price, PriceBasis.KG


def _slip_input(row: SlipRow, ps: ParsedSheet, partner_id: int, site_id: int | None) -> sales.SlipInput:
    # ghi chú đã in nguyên văn ở cột Ghi chú -> biến động tài sản không in lặp lại (show_on_statement=False)
    note_assets = [sales.AssetInput(asset_type=a["asset_type"], direction=a["direction"], qty=a["qty"], sku=a["sku"],
                                    show_on_statement=False)
                   for a in parse_asset_text(f"{row.note or ''} {row.accessory or ''}")]
    if row.note and normalize_text(row.note).startswith("tang"):
        note_assets.append(sales.AssetInput(asset_type=AssetType.GIFT, direction=AssetDirection.GIFT, qty=0,
                                            description=row.note, show_on_statement=False))
    if row.accessory and "cuoc" in normalize_text(row.accessory) and "ko cuoc" not in normalize_text(row.accessory):
        for a in note_assets:
            if a.direction == AssetDirection.LEND:
                a.deposit_amount = abs(row.amount_sheet or ZERO)
    common = dict(partner_id=partner_id, site_id=site_id, slip_date=row.slip_date, vat_invoice_no=row.vat_invoice_no,
                  note=row.note, source_type=SourceType.EXCEL, skip_inventory=True, assets=note_assets,
                  ocr_json={"import": {"sheet": ps.title, "row": row.row}})
    if row.kind == "residual_credit":
        return sales.SlipInput(doc_type=SlipType.RETURN_SALE, items=[sales.ItemInput(
            sku=RESIDUAL_SKU, qty=row.gas_residual_kg, unit_price=row.unit_price or ZERO, price_basis=PriceBasis.UNIT,
            description="Thu hồi gas dư")], **common)
    if row.kind == "charge":
        qty = row.accessory_qty or Decimal(1)
        price = row.unit_price if row.unit_price is not None else abs(row.amount_sheet or ZERO)
        return sales.SlipInput(items=[sales.ItemInput(sku=SERVICE_SKU, qty=qty, unit_price=price,
                                                      price_basis=PriceBasis.UNIT, description=row.accessory)],
                               **common)
    items = []
    residual_price = None
    for sku, qty in row.qty_by_sku.items():
        price, basis = _kg_price(row, sku, ps)
        cap = ps.capacity.get(sku)
        items.append(sales.ItemInput(sku=sku, qty=qty, unit_price=price, price_basis=basis,
                                     capacity_kg=cap if basis == PriceBasis.KG else None))
        if basis == PriceBasis.KG and residual_price is None:
            residual_price = price
    return sales.SlipInput(items=items, gas_residual_kg=row.gas_residual_kg, residual_price=residual_price, **common)


def _dedupe_key(site_id, d: date, vat_no, items_sig, residual, total) -> tuple:
    return (site_id, d, vat_no or "", items_sig, D(residual).normalize(), round_money(total))


# ---------------------------------------------------------------- nhập
def import_workbook(session: Session, path: str | Path, *, partner_code: str | None = None,
                    cutover: str | None = None, today: date | None = None) -> ImportReport:
    path = Path(path)
    report = ImportReport(file=path.name)
    sheets = parse_workbook(path)
    if not sheets:
        raise BusinessError(f"Không tìm thấy sheet Bảng kê nào trong {path.name}")
    report.sheets = len(sheets)
    for ps in sheets:
        report.warnings += ps.warnings
    session.info["user"] = "import-excel"

    with transaction(session):
        if session.scalar(select(masterdata.CompanyProfile.id).limit(1)) is None:
            s0 = sheets[-1].seller
            masterdata.upsert_company(session, parent_name=s0.get("parent_name"), name=s0.get("name") or "CÔNG TY",
                                      address=s0.get("address"), tax_code=s0.get("tax_code"))
        products = _ensure_products(session)
        partner = _ensure_partner(session, sheets, partner_code, report, str(path))
        report.partner_id, report.partner_name = partner.id, partner.name
        names = _site_names(sheets, partner)
        site_ids: dict[str, int] = {}
        for ps in sheets:
            for b in ps.blocks:
                nm = names[id(b)]
                if nm in site_ids:
                    continue
                site = masterdata.find_site(session, nm, partner.id)
                if site is None or normalize_text(site.name) != normalize_text(nm):
                    site = next((s for s in partner.sites if normalize_text(s.name) == normalize_text(nm)), None)
                if site is None:
                    site = masterdata.create_site(session, partner_id=partner.id, name=nm)
                    report.sites_created.append(nm)
                site_ids[nm] = site.id
        # ghi chú đầu tư mới nhất của từng bếp
        for ps in sheets:
            for b in ps.blocks:
                if b.header_note:
                    masterdata.get_site(session, site_ids[names[id(b)]]).investment_note = b.header_note
        partner_id = partner.id
        has_debt_history = bool(session.scalar(select(OpeningBalance.id).where(OpeningBalance.partner_id == partner_id))
                                or session.scalar(select(CashVoucher.id).where(CashVoucher.partner_id == partner_id)))

    # phiếu đã có (để không nhập trùng)
    existing = set()
    for s in session.scalars(select(DeliverySlip).where(DeliverySlip.partner_id == partner_id,
                                                        DeliverySlip.status == DocStatus.POSTED)):
        sig = tuple(sorted((i.product.sku, D(i.qty).normalize()) for i in s.items))
        existing.add(_dedupe_key(s.site_id, s.slip_date, s.vat_invoice_no, sig, s.gas_residual_kg, s.total))

    sheet_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for ps in sheets:
        for b in ps.blocks:
            site_id = site_ids[names[id(b)]]
            for row in b.slips:
                data = _slip_input(row, ps, partner_id, site_id)
                try:
                    res_preview = _preview_total(session, data)
                except BusinessError as exc:
                    report.warnings.append(f"{ps.title} dòng {row.row}: {exc.message}")
                    continue
                sig = tuple(sorted((i.sku, D(i.qty).normalize()) for i in data.items))
                key = _dedupe_key(site_id, data.slip_date, data.vat_invoice_no, sig, data.gas_residual_kg, res_preview)
                sheet_totals[ps.title] += res_preview * (-1 if data.doc_type == SlipType.RETURN_SALE else 1)
                if key in existing:
                    report.slips_skipped_duplicate += 1
                    continue
                if row.amount_sheet is not None:
                    sheet_amt = round_money(row.amount_sheet)
                    signed = res_preview * (-1 if data.doc_type == SlipType.RETURN_SALE else 1)
                    if abs(signed - sheet_amt) > 1:
                        report.warnings.append(f"{ps.title} dòng {row.row}: tính lại {signed:,.0f} ≠ trên file "
                                               f"{sheet_amt:,.0f}")
                try:
                    sales.create_slip(session, data, user="import-excel")
                except BusinessError as exc:
                    report.warnings.append(f"{ps.title} dòng {row.row}: KHÔNG nhập được – {exc.message}")
                    continue
                existing.add(key)
                report.slips_created += 1
            # dòng diễn giải trong khối (đầu tư, tặng...)
            with transaction(session):
                for n in b.notes:
                    if n.note_date is None:
                        continue
                    for a in parse_asset_text(n.text):
                        pid = products[a["sku"]].id if a["sku"] in products else None
                        if a["asset_type"] == AssetType.EMPTY_CYLINDER and pid is None:
                            continue
                        asset_svc.add_movement(session, move_date=n.note_date, partner_id=partner_id, site_id=site_id,
                                               asset_type=a["asset_type"], direction=a["direction"], qty=a["qty"],
                                               product_id=pid, show_on_statement=False,
                                               note=f"Từ bảng kê {ps.title} dòng {n.row}")
                    atype, direction = _classify_note(n.text)
                    if not _note_exists(session, partner_id, site_id, n.note_date, n.text):
                        asset_svc.add_movement(session, move_date=n.note_date, partner_id=partner_id, site_id=site_id,
                                               asset_type=atype, direction=direction, qty=0, description=n.text,
                                               note=f"Từ bảng kê {ps.title} dòng {n.row}")
        _adjust_loans(session, ps, partner_id, names, site_ids, products)

    # ---- bếp không còn trên bảng kê mới nhất: coi như đã thu hồi vỏ/bếp cho mượn (bảng kê cuối là số khách xác nhận)
    last = sheets[-1]
    present = {site_ids[names[id(b)]] for b in last.blocks}
    last_seen: dict[int, str] = {}
    for ps in sheets:
        for b in ps.blocks:
            last_seen[site_ids[names[id(b)]]] = ps.period
    _, last_end = statements.period_bounds(last.period)
    balances = asset_svc.loan_balances(session, partner_id, as_of=last_end)
    with transaction(session):
        for sid, items in balances.items():
            if sid in present or sid is None or not items:
                continue
            site_name = masterdata.get_site(session, sid).name
            _, seen_end = statements.period_bounds(last_seen.get(sid, last.period))
            for (atype, label), val in items.items():
                pid = products[label].id if atype == AssetType.EMPTY_CYLINDER and label in products else None
                direction = AssetDirection.RETURN if val["qty"] > 0 else AssetDirection.LEND
                asset_svc.add_movement(session, move_date=seen_end, partner_id=partner_id, site_id=sid,
                                       asset_type=atype, direction=direction, qty=abs(val["qty"]), product_id=pid,
                                       show_on_statement=False,
                                       note=f"Không còn trên bảng kê sau {last_seen.get(sid)} – coi như đã thu hồi")
            report.assumptions.append(f"{site_name}: không còn trên bảng kê từ sau {last_seen.get(sid)} nên coi như đã thu hồi "
                                      f"{asset_svc.loan_text(items, with_deposit_note=False).replace('Mượn ', '')}")

    # ---- công nợ
    debt_sheets = [ps for ps in sheets if ps.has_debt_section]
    if cutover:
        cut = next((ps for ps in sheets if ps.period == cutover), None)
        if cut is None:
            raise BusinessError(f"Không có sheet cho kỳ {cutover}")
    else:
        cut = debt_sheets[0] if debt_sheets else None
    if has_debt_history:
        report.assumptions.append("Khách đã có số dư/ thanh toán trong phần mềm – không đặt lại công nợ, chỉ thêm "
                                  "các lần thanh toán chưa có.")
    if cut is not None and cut.prior_debts is not None and cut.has_debt_section:
        report.cutover_period = cut.period
        if not has_debt_history:
            remaining = defaultdict(lambda: ZERO)
            for dr in cut.prior_debts:
                remaining[f"{dr.year:04d}-{dr.month:02d}"] += dr.amount
            _set_conversion_balances(session, partner_id, cut.period, remaining)
            report.assumptions.append(
                f"Nợ trước tháng {cut.period} lấy đúng theo sheet {cut.title}: "
                + ", ".join(f"{k}: {v:,.0f}" for k, v in sorted(remaining.items())))
        for ps in sheets:
            if ps.period < cut.period:
                continue
            for pr in ps.payments:
                pay_date = pr.pay_date or ps.date_to or statements.period_bounds(ps.period)[1]
                dup = session.scalar(select(CashVoucher.id).where(
                    CashVoucher.partner_id == partner_id, CashVoucher.voucher_date == pay_date,
                    CashVoucher.amount == round_money(pr.amount), CashVoucher.status == DocStatus.POSTED))
                if dup:
                    report.vouchers_skipped_duplicate += 1
                    continue
                cashbook.create_voucher(session, voucher_type=VoucherType.RECEIPT, voucher_date=pay_date,
                                        amount=pr.amount, fund_type=FundType.BANK, partner_id=partner_id,
                                        description=f"{pr.label} (nhập từ {ps.title})")
                report.vouchers_created += 1
    elif not has_debt_history:
        periods = sorted({s.slip_date.strftime("%Y-%m") for s in session.scalars(
            select(DeliverySlip).where(DeliverySlip.partner_id == partner_id, DeliverySlip.status == DocStatus.POSTED))})
        if periods:
            # tháng mới nhất thường đang dở (chưa chốt) -> coi tháng liền trước cũng chưa thanh toán
            current = (today or date.today()).strftime("%Y-%m")
            latest = periods[-1]
            if latest >= current and len(periods) > 1:
                latest = periods[-2]
            report.cutover_period = latest
            _set_conversion_balances(session, partner_id, latest, {})
            report.assumptions.append(f"File không có phần công nợ/thanh toán: GIẢ ĐỊNH khách đã thanh toán đủ các "
                                      f"tháng trước {latest}; chỉ còn nợ phát sinh từ {latest}. Cần chị xác nhận.")

    # ---- đối chiếu từng sheet
    for ps in sheets:
        check = {"sheet": ps.title, "period": ps.period, "file_total": ps.grand_total_sheet,
                 "app_total": sheet_totals.get(ps.title, ZERO), "file_closing": ps.closing_sheet, "app_closing": None}
        extra = sum((dr.amount for dr in ps.prior_debts if normalize_text(dr.label).startswith("thanh toan thieu")),
                    ZERO)
        ok = ps.grand_total_sheet is None or abs(check["app_total"] + extra - ps.grand_total_sheet) <= len(
            [r for b in ps.blocks for r in b.slips])  # Excel không làm tròn: lệch ≤ 1đ/dòng
        if ps.closing_sheet is not None and report.cutover_period and ps.period >= report.cutover_period:
            st = statements.build_statement(session, partner_id, ps.period)
            check["app_closing"] = st["closing_debt"]
            ok = ok and abs(st["closing_debt"] - round_money(ps.closing_sheet)) <= 1
        check["ok"] = ok
        report.sheet_checks.append(check)
    return report


def _note_exists(session: Session, partner_id: int, site_id: int, d: date, text: str) -> bool:
    from ..models import AssetMovement

    return bool(session.scalar(select(AssetMovement.id).where(
        AssetMovement.partner_id == partner_id, AssetMovement.site_id == site_id, AssetMovement.move_date == d,
        AssetMovement.description == text)))


def _preview_total(session: Session, data: sales.SlipInput) -> Decimal:
    """Tính thành tiền phiếu giống hệt lúc lưu (để chống trùng và đối chiếu) mà không ghi gì."""
    rows, raw_total, default_residual = sales._build_items(session, data)
    slip = DeliverySlip()
    sales._apply_amounts(slip, rows, raw_total, default_residual, data)
    return slip.total


def _adjust_loans(session: Session, ps: ParsedSheet, partner_id: int, names: dict, site_ids: dict, products: dict
                  ) -> None:
    """Chỉnh số vỏ/bếp CN đang mượn của từng bếp cho khớp dòng 'Mượn N vỏ ...' trên sheet (cuối kỳ)."""
    _, end = statements.period_bounds(ps.period)
    with transaction(session):
        session.flush()
        balances = asset_svc.loan_balances(session, partner_id, as_of=end)
        for b in ps.blocks:
            target = parse_loan_total(b.loan_text)
            if not target:
                continue
            site_id = site_ids[names[id(b)]]
            current = {k: v["qty"] for k, v in balances.get(site_id, {}).items()}
            for key in set(target) | {k for k in current if k[0] in (AssetType.EMPTY_CYLINDER, AssetType.STOVE)}:
                diff = target.get(key, ZERO) - current.get(key, ZERO)
                if diff == 0:
                    continue
                atype, label = key
                pid = products[label].id if atype == AssetType.EMPTY_CYLINDER and label in products else None
                if atype == AssetType.EMPTY_CYLINDER and pid is None:
                    continue
                asset_svc.add_movement(session, move_date=end, partner_id=partner_id, site_id=site_id,
                                       asset_type=atype,
                                       direction=AssetDirection.LEND if diff > 0 else AssetDirection.RETURN,
                                       qty=abs(diff), product_id=pid, show_on_statement=False,
                                       note=f"Điều chỉnh cho khớp bảng kê {ps.title}")


def _set_conversion_balances(session: Session, partner_id: int, cut_period: str, remaining: dict[str, Decimal]) -> None:
    """Số dư chuyển đổi: với mỗi tháng trước tháng chuyển đổi, đặt (nợ còn lại theo file − phát sinh đã nhập)."""
    charges = debts.charges_by_period(session, partner_id, DebtSide.RECEIVABLE)
    with transaction(session):
        for period in sorted(set(charges) | set(remaining)):
            if period >= cut_period:
                continue
            adj = remaining.get(period, ZERO) - charges.get(period, ZERO)
            if adj == 0:
                continue
            row = session.scalar(select(OpeningBalance).where(OpeningBalance.partner_id == partner_id,
                                                              OpeningBalance.side == DebtSide.RECEIVABLE,
                                                              OpeningBalance.period == period))
            note = f"Số dư chuyển đổi khi nhập Excel (còn nợ theo file: {remaining.get(period, ZERO):,.0f})"
            if row is None:
                session.add(OpeningBalance(partner_id=partner_id, side=DebtSide.RECEIVABLE, period=period,
                                           amount=adj, note=note))
            else:
                row.amount, row.note = adj, note


def month_end(period: str) -> date:
    y, m = (int(x) for x in period.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])
