# Aria ITOps Portal — Podman/Docker image
# Build:  podman build -t aria-itops .
# Run:    see CLAUDE.md → "Run in Podman"

FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    DB_PATH=/app/data/users.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py create_user.py ./
COPY static ./static

# users.db lives on a volume mounted at /app/data so it survives rebuilds
RUN useradd --uid 1000 --create-home appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

EXPOSE 8002
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
