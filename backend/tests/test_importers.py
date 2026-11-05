"""Nhập Excel bảng kê cũ + XML hóa đơn điện tử."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import transaction
from app.errors import BusinessError
from app.importers.einvoice_xml import import_einvoice, parse_einvoice
from app.importers.history import import_workbook, parse_asset_text, parse_loan_total
from app.models import DeliverySlip, PartnerType, SourceType
from app.services import masterdata, statements

FIX = Path(__file__).parent / "fixtures"


def test_asset_text_parsing():
    assert [(a["direction"], a["qty"], a["sku"]) for a in parse_asset_text("Thu 6 vỏ pc45 và mượn 6 vỏ pc48")] == [
        ("RETURN", 6, "PC45"), ("LEND", 6, "PC48")]
    assert parse_loan_total("Mượn 30 vỏ pc48- ko cược + 13 bếp CN") == {
        ("EMPTY_CYLINDER", "PC48"): 30, ("STOVE", "bếp CN"): 13}


def test_import_stargate_october_sheet(session):
    rep = import_workbook(session, FIX / "stg_t10_2026.xlsx")
    assert rep.slips_created == 33 and rep.vouchers_created == 6
    assert rep.cutover_period == "2026-10"
    assert not rep.mismatches and not rep.warnings
    st = statements.build_statement(session, rep.partner_id, "2026-10")
    assert st["grand_total"]["amount"] == Decimal("1195917000")
    assert st["closing_debt"] == Decimal("1829047800")
    assert st["closing_loans_text"] == "Tổng mượn 184 vỏ PC48 + 13 bếp CN"
    assert [p["note"] for p in st["payments"]][-1] == "TT 1 phần CN tháng 09/2026"
    slip = session.query(DeliverySlip).filter_by(vat_invoice_no="4638").one()
    assert slip.total == Decimal("52706000") and slip.source_type == SourceType.EXCEL
    assert masterdata.get_partner(session, rep.partner_id).tax_code == "0101358112"
    # nhập lại không trùng
    again = import_workbook(session, FIX / "stg_t10_2026.xlsx")
    assert again.slips_created == 0 and again.slips_skipped_duplicate == 33
    assert again.vouchers_created == 0 and again.vouchers_skipped_duplicate == 6
    assert statements.build_statement(session, rep.partner_id, "2026-10")["closing_debt"] == Decimal("1829047800")


NS = 'xmlns="http://laphoadon.gdt.gov.vn/2014/09/invoicexml/v1"'


def _xml(number: str, *, seller_mst="0100100100", buyer_mst="0107776927", lines: str = "", total="51840000",
         before="48000000", vat="3840000") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TDiep><DLieu><HDon {NS}><DLHDon Id="data">
 <TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TAA</KHHDon><SHDon>{number}</SHDon><NLap>2026-10-02</NLap>
  <DVTTe>VND</DVTTe><TGia>1</TGia><HTTToan>TM/CK</HTTToan></TTChung>
 <NDHDon>
  <NBan><Ten>CÔNG TY GAS PETROLIMEX HÀ NỘI</Ten><MST>{seller_mst}</MST><DChi>Hà Nội</DChi></NBan>
  <NMua><Ten>CÔNG TY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI</Ten><MST>{buyer_mst}</MST><DChi>Tây Mỗ</DChi></NMua>
  <DSHHDVu>{lines or '''
   <HHDVu><TChat>1</TChat><STT>1</STT><MHHDVu>LPG48</MHHDVu><THHDVu>Gas hóa lỏng bình 48kg</THHDVu>
    <DVTinh>Bình</DVTinh><SLuong>30</SLuong><DGia>1600000</DGia><ThTien>48000000</ThTien><TSuat>8%</TSuat></HHDVu>
   <HHDVu><TChat>4</TChat><STT>2</STT><THHDVu>Giao tại kho Tây Mỗ</THHDVu></HHDVu>'''}
  </DSHHDVu>
  <TToan><TgTCThue>{before}</TgTCThue><TgTThue>{vat}</TgTThue><TgTTTBSo>{total}</TgTTTBSo>
   <TgTTTBChu>Năm mươi mốt triệu tám trăm bốn mươi nghìn đồng</TgTTTBChu></TToan>
 </NDHDon></DLHDon></HDon></DLieu></TDiep>""".encode("utf-8")


@pytest.fixture()
def company(session):
    with transaction(session):
        masterdata.upsert_company(session, name="CTY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI", tax_code="0107776927")
        masterdata.create_product(session, sku="PC48", name="Gas 48kg", capacity_kg=48)


def test_parse_einvoice_namespaces():
    inv = parse_einvoice(_xml("123"))
    assert (inv.symbol, inv.number, inv.issue_date) == ("C26TAA", "123", date(2026, 10, 2))
    assert inv.seller.tax_code == "0100100100" and inv.buyer.tax_code == "0107776927"
    assert inv.lines[0].vat_rate == Decimal("0.08") and inv.lines[1].nature == "4"
    assert inv.total == Decimal("51840000")
    with pytest.raises(BusinessError):
        parse_einvoice(b"<abc/>")


def test_purchase_xml_mapping_remembered(session, company):
    res = import_einvoice(session, _xml("123"))
    assert res.direction == "PURCHASE" and res.status == "NEEDS_MAPPING"
    assert res.unmapped[0]["code"] == "LPG48"
    res = import_einvoice(session, _xml("123"), mapping={1: {"sku": "PC48"}})
    assert res.status == "IMPORTED"
    pc48 = masterdata.get_product_by_sku(session, "PC48")
    assert pc48.current_stock == 30 and pc48.cost_price == Decimal("1600000")
    supplier = masterdata.find_partner_by_tax_code(session, "0100100100")
    assert supplier.partner_type == PartnerType.SUPPLIER
    assert import_einvoice(session, _xml("123")).status == "DUPLICATE"
    # hóa đơn sau của cùng NCC: tự nối mã đã nhớ
    res2 = import_einvoice(session, _xml("124"))
    assert res2.status == "IMPORTED" and masterdata.get_product_by_sku(session, "PC48").current_stock == 60


def test_sales_xml_links_slips(session, demo):
    xml = _xml("4638", seller_mst="0107776927", buyer_mst="0101358112", total="52706000", before="48801852",
               vat="3904148")
    res = import_einvoice(session, xml)
    assert res.direction == "SALE" and res.status == "IMPORTED"
    assert len(res.linked_slip_ids) == 1 and not res.warnings
    assert import_einvoice(session, xml).status == "DUPLICATE"
