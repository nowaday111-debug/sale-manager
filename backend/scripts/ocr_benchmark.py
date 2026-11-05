"""Chấm điểm các hãng AI đọc phiếu trên 18 ảnh thật (benchmark/images) so với đáp án (benchmark/ground_truth.json).

Chạy trong thư mục backend (cần khóa API trong file .env):
    python -m scripts.ocr_benchmark                       # các hãng đã có khóa
    python -m scripts.ocr_benchmark --providers deepseek  # chỉ DeepSeek V4.1 Flash
    python -m scripts.ocr_benchmark --providers gemini --model gemini-3.8-flash --limit 5
Kết quả chi tiết: benchmark/results/<hãng>_<mô hình>.json
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.config import BASE_DIR
from app.errors import BusinessError
from app.models import AssetDirection, AssetType
from app.ocr.normalize import normalize
from app.ocr.preprocess import prepare
from app.ocr.prompt import SYSTEM_PROMPT, USER_PROMPT
from app.ocr.providers import VisionClient, available_providers

BENCH = BASE_DIR / "benchmark"
TODAY = date(2026, 11, 4)
FIELDS = ["document_type", "slip_no", "date", "gas", "unit_price", "gas_residual_kg", "total", "lend", "return"]

# Giá USD / 1 triệu token (vào, ra) – lấy từ trang chính thức ngày 04/11/2026, chỉ để ƯỚC TÍNH.
PRICES = {
    "deepseek": (0.30, 1.20),          # giờ cao điểm; ngoài giờ cao điểm rẻ 1/2
    "gemini": (0.25, 1.50),            # gemini-3.1-flash-lite
    "qwen": (0.07, 0.16),              # qwen-vl-ocr, Singapore
    "openai": (0.10, 0.50),            # gpt-6-luna
    "anthropic": (1.00, 5.00),
}


def _dec(v):
    return None if v is None else Decimal(str(v))


def _qty_map(d: dict) -> dict:
    return {k: Decimal(str(v)) for k, v in (d or {}).items() if v}


def extract(ns) -> dict:
    gas = defaultdict(Decimal)
    for g in ns.gas_lines:
        if g.sku and g.qty:
            gas[g.sku] += g.qty
    lend, ret = defaultdict(Decimal), defaultdict(Decimal)
    for a in ns.assets:
        if a.asset_type != AssetType.EMPTY_CYLINDER or not a.sku or not a.qty:
            continue
        if a.direction == AssetDirection.LEND:
            lend[a.sku] += a.qty
        elif a.direction == AssetDirection.RETURN:
            ret[a.sku] += a.qty
    price = next((g.unit_price for g in ns.gas_lines if g.unit_price), None)
    return {
        "document_type": "HOA_DON_BAN_LE" if ns.is_slip else "KHAC",
        "slip_no": ns.slip_no,
        "date": ns.slip_date.isoformat() if ns.slip_date else None,
        "gas": dict(gas), "unit_price": price, "gas_residual_kg": ns.residual_kg,
        "total": ns.total_written if ns.total_written is not None else ns.total_calc,
        "lend": dict(lend), "return": dict(ret),
    }


def compare(truth: dict, got: dict) -> dict[str, bool]:
    res = {}
    for f in FIELDS:
        if f not in truth or (f == "date" and truth[f] is None):
            continue
        t = truth[f]
        g = got.get(f)
        if f in ("gas", "lend", "return"):
            res[f] = _qty_map(t) == _qty_map(g)
        elif f in ("unit_price", "total"):
            if t is None:
                res[f] = g is None or f == "total"     # không chấm tổng khi phiếu để trống
                continue
            res[f] = g is not None and abs(_dec(g) - _dec(t)) <= max(Decimal(1000), _dec(t) * Decimal("0.001"))
        elif f == "gas_residual_kg":
            res[f] = _dec(g or 0) == _dec(t)
        else:
            res[f] = (g or None) == t
    return res


def run(provider: str, model: str | None, limit: int | None) -> dict:
    truth_all = json.loads((BENCH / "ground_truth.json").read_text(encoding="utf-8"))["slips"]
    if limit:
        truth_all = truth_all[:limit]
    client = VisionClient(provider, model)
    rows, tokens_in, tokens_out, seconds = [], 0, 0, 0.0
    field_ok = defaultdict(int)
    field_n = defaultdict(int)
    for truth in truth_all:
        img = (BENCH / "images" / truth["file"]).read_bytes()
        started = time.monotonic()
        try:
            res = client.read(prepare(img, truth["file"]), SYSTEM_PROMPT, USER_PROMPT)
        except BusinessError as exc:
            rows.append({"file": truth["file"], "error": exc.message})
            for f in FIELDS:
                if f in truth and not (f == "date" and truth[f] is None):
                    field_n[f] += 1
            print(f"  {truth['file']:<24} LỖI: {exc.message[:80]}")
            continue
        seconds += time.monotonic() - started
        usage = res.usage or {}
        tokens_in += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        tokens_out += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        ns = normalize(res.data, today=TODAY)
        got = extract(ns)
        cmp = compare(truth, got)
        for f, ok in cmp.items():
            field_n[f] += 1
            field_ok[f] += int(ok)
        wrong = [f for f, ok in cmp.items() if not ok]
        print(f"  {truth['file']:<24} {sum(cmp.values())}/{len(cmp)} đúng" + (f"  sai: {', '.join(wrong)}" if wrong else ""))
        rows.append({"file": truth["file"], "score": cmp, "got": got, "truth": truth,
                     "flags": [f.__dict__ for f in ns.flags], "raw": res.data})
    total_ok, total_n = sum(field_ok.values()), sum(field_n.values())
    p_in, p_out = PRICES.get(provider, (0, 0))
    cost = tokens_in / 1e6 * p_in + tokens_out / 1e6 * p_out
    summary = {
        "provider": provider, "model": client.model, "images": len(truth_all),
        "accuracy": round(total_ok / total_n, 4) if total_n else 0,
        "by_field": {f: f"{field_ok[f]}/{field_n[f]}" for f in FIELDS if field_n[f]},
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost, 4),
        "cost_usd_per_500_slips": round(cost / max(len(truth_all), 1) * 500, 3),
        "seconds_per_image": round(seconds / max(len(truth_all), 1), 2),
    }
    out = BENCH / "results"
    out.mkdir(exist_ok=True)
    (out / f"{provider}_{client.model}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", help="danh sách cách nhau dấu phẩy; mặc định: các hãng đã có khóa trong .env")
    ap.add_argument("--model", help="ghi đè mô hình (chỉ khi chạy 1 hãng)")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    names = args.providers.split(",") if args.providers else available_providers()
    if not names:
        raise SystemExit("Chưa có khóa API nào. Sao chép .env.example thành .env và điền khóa (VD DEEPSEEK_API_KEY).")
    summaries = []
    for name in names:
        print(f"\n=== {name} ===")
        summaries.append(run(name.strip(), args.model if len(names) == 1 else None, args.limit))
    print("\nKẾT QUẢ")
    print(f"{'Hãng':<10} {'Mô hình':<24} {'Đúng':>7} {'s/ảnh':>6} {'$/500 phiếu':>12}  Theo trường")
    for s in sorted(summaries, key=lambda x: -x["accuracy"]):
        print(f"{s['provider']:<10} {s['model']:<24} {s['accuracy']:>7.1%} {s['seconds_per_image']:>6} "
              f"{s['cost_usd_per_500_slips']:>12}  {s['by_field']}")


if __name__ == "__main__":
    main()
