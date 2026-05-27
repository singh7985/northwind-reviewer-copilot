# --- backend/Dockerfile -----------------------------------------------------
# Production image for the FastAPI service.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: pypdf needs nothing, but psycopg[binary] does include libpq.
# Keep image lean.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# App + policy library + sample submissions (used by /admin/seed and demo buttons)
COPY backend /app/backend
COPY policies /app/policies
COPY submissions /app/submissions
COPY data /app/data
RUN mkdir -p /app/backend/uploads

ENV PYTHONPATH=/app/backend

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
