# Sale-manager – chạy bằng Docker trên VPS Linux:  docker compose up -d
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SALE_DB_URL=sqlite:////data/sale.db SALE_UPLOAD_DIR=/data/uploads SALE_EXPORT_DIR=/data/exports
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
RUN useradd --system --uid 1001 salemgr && mkdir -p /data && chown -R salemgr /data /app
USER salemgr
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
