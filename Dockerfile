# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app --home /nonexistent app

COPY pyproject.toml README.md ./
COPY app ./app
COPY data ./data
RUN pip install --no-cache-dir .

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
