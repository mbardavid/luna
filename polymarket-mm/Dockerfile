# ── Stage 1: Builder ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY . .

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd -r mmbot && useradd -r -g mmbot mmbot

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build /app

RUN chown -R mmbot:mmbot /app
USER mmbot

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

CMD ["python", "-m", "core.main"]
