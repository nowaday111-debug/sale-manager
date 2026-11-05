"""Sao lưu file dữ liệu SQLite (an toàn cả khi phần mềm đang chạy – dùng API backup của SQLite).

- Tự sao lưu 1 lần/ngày khi khởi động phần mềm vào data/backups/sale-YYYYMMDD.db, giữ 30 bản gần nhất.
- Chạy tay:  python -m app.backup            (hoặc nút "Sao lưu ngay" ở màn hình Cài đặt)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .config import settings

KEEP = 30


def sqlite_path(url: str | None = None) -> Path | None:
    url = url or settings.database_url
    if not url.startswith("sqlite:///"):
        return None
    p = url[len("sqlite:///"):]
    return Path(p) if p and p != ":memory:" else None


def backup_now(*, force: bool = False, dest_dir: Path | None = None) -> Path | None:
    src = sqlite_path()
    if src is None or not src.is_file():
        return None
    dest_dir = dest_dir or src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S" if force else "%Y%m%d")
    dest = dest_dir / f"{src.stem}-{stamp}.db"
    if dest.exists() and not force:
        return dest
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    olds = sorted(dest_dir.glob(f"{src.stem}-*.db"))
    for old in olds[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


if __name__ == "__main__":
    out = backup_now(force=True)
    print(f"Đã sao lưu: {out}" if out else "Không tìm thấy file dữ liệu SQLite để sao lưu")
