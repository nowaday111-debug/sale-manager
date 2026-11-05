from ..db import engine, init_db
from . import _all_versions, applied_versions

if __name__ == "__main__":
    init_db()
    with engine.connect() as conn:
        done = applied_versions(conn)
    for name in _all_versions():
        print(("[x] " if name in done else "[ ] ") + name)
