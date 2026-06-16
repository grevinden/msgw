FROM alpine
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

LABEL maintainer="anton@79252587842.ru"
LABEL description="MessageCenter – WebSocket/HTTP шлюз"
LABEL org.opencontainers.image.title="MessageCenter"
LABEL org.opencontainers.image.description="WebSocket/HTTP шлюз с гарантированной доставкой"
LABEL org.opencontainers.image.version="0.0.0"


ENV \
PYTHONOPTIMIZE=1 \
TZ=Europe/Moscow \
UV_NO_DEV=1 \
UV_FROZEN=1 \
UV_LINK_MODE=copy

ENV \
UVICORN_WORKERS=1 \
UVICORN_LOG_LEVEL=info \
UVICORN_ACCESS_LOG=true \
UVICORN_REUSE_PORT=false \
UVICORN_LIMIT_CONCURRENCY=500 \
UVICORN_BACKLOG=2048 \
UVICORN_LOOP=uvloop \
UVICORN_HTTP=httptools \
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=60 \
UVICORN_TIMEOUT_KEEP_ALIVE=3 \
UVICORN_WS=websockets-sansio \
UVICORN_WS_PING_INTERVAL=3 \
UVICORN_WS_PING_TIMEOUT=2 \
UVICORN_WS_PER_MESSAGE_DEFLATE=1

# ---- Application environment (prefix MSGW_ → parsed by pydantic-settings) ----
ENV APP=MSGW
ENV MSGW_CACHE_URL="mem://?check_interval=1"
ENV MSGW_CACHE_TTL=3600
#ENV MSGW_ECIES_KEY=""

RUN apk add --no-cache tzdata gcc musl-dev python3-dev libffi-dev openssl-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project

COPY --link . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

# Health check (requires / endpoint to exist)
HEALTHCHECK --interval=10s --timeout=2s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8000/ || exit 1

ENTRYPOINT [ ".venv/bin/fastapi" ]
CMD [ "run", "--host=0.0.0.0", "--port=8000", "--worker=1", "--forwarded-allow-ips=*", "src/msgw"]

EXPOSE 8000
