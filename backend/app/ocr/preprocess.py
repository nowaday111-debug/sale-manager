"""Tiền xử lý ảnh phiếu trước khi gửi AI: xoay đúng chiều, thu nhỏ, tăng tương phản nhẹ, nén JPEG."""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageOps

from ..errors import BusinessError

MAX_SIDE = 2048
JPEG_QUALITY = 88


def _pdf_first_page(data: bytes) -> Image.Image:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - phụ thuộc môi trường
        raise BusinessError("Đọc PDF cần cài thư viện pypdfium2 (pip install pypdfium2)") from exc
    pdf = pdfium.PdfDocument(data)
    if len(pdf) == 0:
        raise BusinessError("File PDF không có trang nào")
    page = pdf[0]
    return page.render(scale=200 / 72).to_pil()


def load_image(data: bytes, filename: str = "") -> Image.Image:
    if not data:
        raise BusinessError("File ảnh rỗng")
    if filename.lower().endswith(".pdf") or data[:5] == b"%PDF-":
        img = _pdf_first_page(data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:
            raise BusinessError(f"Không đọc được ảnh '{filename}': {exc}") from exc
    return img


def prepare(data: bytes, filename: str = "", *, max_side: int = MAX_SIDE, enhance: bool = True) -> bytes:
    """Trả về JPEG đã chuẩn hóa để gửi lên Vision AI."""
    img = load_image(data, filename)
    img = ImageOps.exif_transpose(img)          # ảnh chụp điện thoại bị xoay
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if enhance:
        img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
        img = ImageEnhance.Contrast(img).enhance(1.15)    # nét bút bi mờ / giấy than
        img = ImageEnhance.Sharpness(img).enhance(1.2)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
