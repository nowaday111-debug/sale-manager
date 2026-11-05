"""Đăng nhập HTTP Basic tùy chọn – BẮT BUỘC bật khi đưa phần mềm lên VPS / Internet.

Bật bằng biến môi trường (backend/.env):

    SALE_AUTH_USERS=hien:pbkdf2~...:CHIEF_ACCOUNTANT,lan:pbkdf2~...:STAFF

Mỗi người: ``tên:mật_khẩu_đã_băm:VAI_TRÒ``. Tạo chuỗi băm:  ``python -m app.auth hash``
(cho phép ghi mật khẩu thường dạng ``plain~matkhau`` để thử nghiệm, KHÔNG dùng trên Internet).

Khi bật: mọi request (trừ /api/health) phải đăng nhập; tên người dùng & vai trò lấy từ tài khoản – header
X-User / X-Role do trình duyệt gửi bị bỏ qua (nhân viên không tự nâng quyền được).
Khi không bật (máy nội bộ Windows): giữ hành vi cũ, vai trò lấy theo header hoặc SALE_DEFAULT_ROLE.
"""
from __future__ import annotations

import base64
import binascii
import contextvars
import getpass
import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass

current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_user", default=None)
current_role: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_role", default=None)

_ITER = 260_000


def acting_user() -> str:
    """Người đang thao tác: tài khoản đăng nhập (nếu bật SALE_AUTH_USERS) hoặc SALE_USER."""
    from .config import settings

    return current_user.get() or settings.default_user


def acting_role() -> str | None:
    return current_role.get()


@dataclass(frozen=True)
class Account:
    username: str
    secret: str
    role: str


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), _ITER).hex()
    return f"pbkdf2~{salt}~{digest}"


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("plain~"):
        return hmac.compare_digest(password, stored[6:])
    try:
        _, salt, digest = stored.split("~", 2)
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), _ITER).hex()
    return hmac.compare_digest(calc, digest)


def load_accounts(raw: str | None = None) -> dict[str, Account]:
    raw = os.getenv("SALE_AUTH_USERS", "") if raw is None else raw
    accounts: dict[str, Account] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) < 2:
            continue
        username, secret = parts[0].strip(), parts[1].strip()
        role = (parts[2].strip().upper() if len(parts) > 2 and parts[2].strip() else "STAFF")
        accounts[username] = Account(username, secret, role)
    return accounts


def check_basic(header: str | None, accounts: dict[str, Account]) -> Account | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, _, password = decoded.partition(":")
    acc = accounts.get(username)
    if acc and verify_password(password, acc.secret):
        return acc
    return None


def _cli() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "hash":
        pw = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("Mật khẩu: ")
        print(hash_password(pw))
        return
    print("Dùng:  python -m app.auth hash [mật_khẩu]")


if __name__ == "__main__":
    _cli()
