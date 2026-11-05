"""Cấu hình ứng dụng, đọc từ biến môi trường (có giá trị mặc định chạy ngay được)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend


def _load_dotenv(path: Path) -> None:
    """Đọc file backend/.env (KEY=VALUE) nếu có. Biến đã đặt trong hệ thống được ưu tiên."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # sqlite:///đường_dẫn  hoặc  postgresql+psycopg://user:pass@host/db
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "SALE_DB_URL", f"sqlite:///{(BASE_DIR.parent / 'data' / 'sale.db').as_posix()}"
        )
    )
    # Cho phép xuất kho âm (chỉ cảnh báo). Đặt SALE_ALLOW_NEGATIVE_STOCK=0 để chặn.
    allow_negative_stock: bool = field(default_factory=lambda: _bool("SALE_ALLOW_NEGATIVE_STOCK", True))
    # Kiểu đọc số tiền bằng chữ: "excel" (giống file bảng kê hiện tại: "ngàn", có dấu phẩy) | "standard"
    amount_words_style: str = field(default_factory=lambda: os.getenv("SALE_WORDS_STYLE", "excel"))
    # Tên người dùng ghi vào audit log khi không có đăng nhập
    default_user: str = field(default_factory=lambda: os.getenv("SALE_USER", "admin"))
    # Vai trò mặc định khi request không gửi header X-Role: ADMIN | MANAGER | CHIEF_ACCOUNTANT | ACCOUNTANT | STAFF
    default_role: str = field(default_factory=lambda: os.getenv("SALE_DEFAULT_ROLE", "ADMIN").strip().upper())
    upload_dir: Path = field(default_factory=lambda: Path(os.getenv("SALE_UPLOAD_DIR", str(BASE_DIR.parent / "data" / "uploads"))))
    # ---- AI đọc ảnh phiếu (OCR) ----
    # deepseek | gemini | qwen | openai | anthropic
    ocr_provider: str = field(default_factory=lambda: os.getenv("OCR_PROVIDER", "deepseek"))
    ocr_model: str | None = field(default_factory=lambda: os.getenv("OCR_MODEL") or None)
    ocr_timeout: float = field(default_factory=lambda: float(os.getenv("OCR_TIMEOUT", "120")))
    export_dir: Path = field(default_factory=lambda: Path(os.getenv("SALE_EXPORT_DIR", str(BASE_DIR.parent / "data" / "exports"))))


settings = Settings()
