"""AI đọc phiếu: chạy toàn bộ luồng với 'AI giả' (không gọi mạng)."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.models import DeliverySlip, OcrJobStatus
from app.ocr import service
from app.ocr.normalize import normalize, parse_number
from app.ocr.providers import VisionClient, extract_json

BENCH = Path(__file__).resolve().parents[1] / "benchmark"

AI_0017530 = {
    "document_type": "HOA_DON_BAN_LE", "slip_no": "0017530", "customer": "Stagate", "address": "Hải Phòng",
    "date": {"day": 8, "month": 8, "year_text": "202 6"},
    "lines": [
        {"kind": "GAS", "text": "pc48 13 38,500 24024", "product": "PC48", "qty": 13, "unit_price_raw": "38,500",
         "amount_raw": "24024"},
        {"kind": "GAS_RESIDUAL", "text": "gadư 5,6kg -2156", "residual_kg_raw": "5,6", "amount_raw": "-2156"},
        {"kind": "NOTE", "text": "(Cty cho mượn 03 vỏ PC48)"},
    ],
    "total_raw": "21868",
    "assets": [{"asset_type": "EMPTY_CYLINDER", "direction": "LEND", "qty": 3, "product": "PC48",
                "text": "(Cty cho mượn 03 vỏ PC48)"}],
    "customer_note": "CT²", "staff": ["Quân", "Dũng", "Đông"], "customer_signed": True, "confidence": 0.86,
}


def _mock_client(answer: dict, captured: list | None = None) -> VisionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if captured is not None:
            captured.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}}],
                                         "usage": {"prompt_tokens": 1500, "completion_tokens": 400}})
    return VisionClient("deepseek", api_key="test-key", transport=httpx.MockTransport(handler))


def test_parse_number_vietnamese_styles():
    assert parse_number("38,500") == Decimal("38500")
    assert parse_number("5,6") == Decimal("5.6")
    assert parse_number("2.675") == Decimal("2675")
    assert parse_number("-625") == Decimal("-625")
    assert parse_number("x") is None and parse_number("") is None
    assert extract_json('Kết quả:\n{"a": 1}') == {"a": 1}


def test_normalize_fixes_residual_and_scales_money():
    ns = normalize(AI_0017530, today=date(2026, 11, 4))
    g = ns.gas_lines[0]
    assert (g.sku, g.qty, g.unit_price, g.price_basis) == ("PC48", 13, Decimal("38500"), "KG")
    assert ns.residual_kg == Decimal("56")                 # viết 5,6 nhưng tiền trừ 2.156.000 = 56 kg
    assert ns.total_written == ns.total_calc == Decimal("21868000")
    assert ns.slip_date == date(2026, 8, 8)
    assert any("56" in f.message for f in ns.flags)
    assert ns.needs_review


def test_per_unit_price_slip():
    raw = {"document_type": "HOA_DON_BAN_LE", "slip_no": "0011117", "date": {"day": 3, "month": 3, "year_text": "2024"},
           "lines": [{"kind": "GAS", "text": "pc45 04 1100 4400", "product": "PC45", "qty": 4, "unit_price_raw": "1100",
                      "amount_raw": "4400"},
                     {"kind": "GAS_RESIDUAL", "text": "gas dư 25,6kg -625", "residual_kg_raw": "25,6", "amount_raw": "-625"},
                     {"kind": "DEDUCTION", "text": "Trừ tiền đầu tư còn", "amount_raw": "-1100"}],
           "total_raw": "2.675", "confidence": 0.9}
    ns = normalize(raw, today=date(2024, 3, 5))
    assert ns.gas_lines[0].price_basis == "UNIT" and ns.gas_lines[0].unit_price == Decimal("1100000")
    assert ns.residual_kg == Decimal("25.6") and ns.deduction == Decimal("1100000")
    assert ns.total_written == Decimal("2675000")
    assert not [f for f in ns.flags if f.level == "warning"]


def test_not_a_slip():
    ns = normalize({"document_type": "KHAC", "confidence": 0.95})
    assert not ns.is_slip and ns.needs_review


def test_full_flow_create_job_and_apply(session, demo, tmp_path, monkeypatch):
    monkeypatch.setattr(service.settings.__class__, "upload_dir", property(lambda self: tmp_path), raising=False)
    sent = []
    img = (BENCH / "images" / "0017530.jpg").read_bytes()
    job = service.process_image(session, img, "0017530.jpg", client=_mock_client(AI_0017530, sent),
                                today=date(2026, 11, 4))
    assert sent[0]["model"] == "deepseek-flash"
    assert sent[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert job.status == OcrJobStatus.PENDING_REVIEW
    proposal = json.loads(job.result_json)["proposal"]
    assert proposal["site_name"] == "Bếp Stargate Hải Phòng"      # khớp tên viết tay "Stagate / Hải Phòng"
    assert proposal["kind"] == "SLIP" and proposal["needs_review"] is True
    applied = service.apply_job(session, job.id)
    slip = session.get(DeliverySlip, applied.slip_id)
    assert slip.total == Decimal("21868000") and slip.source_type == "AI_OCR"
    assert slip.gas_residual_kg == Decimal("56")
    with pytest.raises(Exception):
        service.apply_job(session, job.id)                         # không ghi sổ 2 lần
    # phiếu đã nhập -> lần đọc sau được cảnh báo trùng
    job2 = service.process_image(session, img, "0017530.jpg", client=_mock_client(AI_0017530),
                                 today=date(2026, 11, 4))
    flags = json.loads(job2.result_json)["proposal"]["flags"]
    assert any("đã nhập" in f["message"] for f in flags)


def test_price_from_price_list_when_blank(session, demo, tmp_path, monkeypatch):
    monkeypatch.setattr(service.settings.__class__, "upload_dir", property(lambda self: tmp_path), raising=False)
    answer = {"document_type": "HOA_DON_BAN_LE", "slip_no": "0015883", "customer": "Bếp Stagbet", "address": "Đan Phượng",
              "date": {"day": 1, "month": 7, "year_text": "2026"},
              "lines": [{"kind": "GAS", "text": "pc48 15 x x", "product": "PC48", "qty": 15, "unit_price_raw": "x",
                         "amount_raw": "x"}],
              "assets": [{"asset_type": "EMPTY_CYLINDER", "direction": "LEND", "qty": 15, "product": "PC48",
                          "text": "Cty cho nhà hàng mượn 15 vỏ PC48"}], "confidence": 0.9}
    job = service.process_image(session, (BENCH / "images" / "0015883.jpg").read_bytes(),
                                "0015883.jpg", client=_mock_client(answer), today=date(2026, 11, 4))
    proposal = json.loads(job.result_json)["proposal"]
    assert proposal["site_name"] == "Bếp Đan Phượng - Hà Nội"
    assert proposal["items"][0]["unit_price"] == "47500"
    slip = session.get(DeliverySlip, service.apply_job(session, job.id).slip_id)
    assert slip.total == Decimal("34200000")                       # 15 × 48 × 47.500


def test_missing_key_gives_clear_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(Exception) as exc:
        VisionClient("deepseek")
    assert "DEEPSEEK_API_KEY" in str(exc.value)
