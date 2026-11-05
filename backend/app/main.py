"""Điểm vào FastAPI.  Chạy:  uvicorn app.main:app --reload   (trong thư mục backend)."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi import encoders as _fastapi_encoders
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import check_basic, current_role, current_user, load_accounts
from .backup import backup_now
from .config import BASE_DIR
from .db import init_db
from .errors import BusinessError
from .routers import finance, imports, inventory, masterdata, ocr, orders_flow, sales


def _default(obj):
    if isinstance(obj, Decimal):
        # giữ nguyên độ chính xác: trả về chuỗi số ("1829047800", "42.4")
        return format(obj.normalize(), "f") if obj == obj.to_integral_value() else format(obj, "f")
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Không chuyển được sang JSON: {type(obj)}")


# FastAPI mặc định đổi Decimal -> float trước khi trả JSON; ép về chuỗi để giữ chính xác tuyệt đối.
_fastapi_encoders.ENCODERS_BY_TYPE[Decimal] = _default


class DecimalJSONResponse(JSONResponse):
    """Tiền trả về dạng chuỗi để không bị đổi sang float ở phía trình duyệt."""

    def render(self, content) -> bytes:
        return json.dumps(content, default=_default, ensure_ascii=False).encode("utf-8")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        backup_now()          # tự sao lưu 1 lần/ngày vào data/backups
    except Exception:  # noqa: BLE001 – sao lưu lỗi không được chặn phần mềm chạy
        pass
    yield


app = FastAPI(title="Sale-manager – Quản lý bán gas, kho, công nợ", version="0.6.0",
              default_response_class=DecimalJSONResponse, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


_ACCOUNTS = load_accounts()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Bật khi có SALE_AUTH_USERS (VPS / Internet). Không bật thì bỏ qua."""
    if not _ACCOUNTS or request.url.path == "/api/health":
        return await call_next(request)
    acc = check_basic(request.headers.get("authorization"), _ACCOUNTS)
    if acc is None:
        return Response(status_code=401, content="Cần đăng nhập", media_type="text/plain; charset=utf-8",
                        headers={"WWW-Authenticate": 'Basic realm="Sale-manager", charset="UTF-8"'})
    t1, t2 = current_user.set(acc.username), current_role.set(acc.role)
    try:
        return await call_next(request)
    finally:
        current_user.reset(t1)
        current_role.reset(t2)


@app.get("/api/me")
def me():
    return {"user": current_user.get(), "role": current_role.get(), "auth_enabled": bool(_ACCOUNTS)}


@app.exception_handler(BusinessError)
async def business_error_handler(_request: Request, exc: BusinessError):
    return DecimalJSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/backup")
def backup():
    path = backup_now(force=True)
    if path is None:
        raise BusinessError("Chỉ sao lưu được khi dùng file dữ liệu SQLite")
    return {"path": str(path)}


for r in (masterdata.router, sales.router, inventory.router, finance.router, ocr.router, imports.router,
          orders_flow.router):
    app.include_router(r)


# Giao diện web (Bước 5): thư mục ../frontend, mở http://127.0.0.1:8000/
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
