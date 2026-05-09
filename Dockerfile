FROM alpine
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

LABEL maintainer="anton@79252587842.ru"
LABEL description="MessageCenter – WebSocket/HTTP шлюз"

ENV \
PYTHONOPTIMIZE=1 \
TZ=Europe/Moscow \
UV_NO_DEV=1 \
UV_FROZEN=1

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

ENV \
APP=MSGW \
# URL для хранилища кеша (mem://, mongo://..., redis://...)
MSGW_CACHE="mem://?check_interval=1" \
# количество ключей, обрабатываемых за раз при сканировании
MSGW_CACHE_BATCH_SIZE=100 \
# время жизни ключей (в секундах) по-умолчанию
MSGW_CACHE_TTL=3600

RUN apk add --no-cache tzdata gcc musl-dev python3-dev libffi-dev openssl-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project

COPY --link . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

ENTRYPOINT ["uv", "run", "--no-dev", "uvicorn", "msgw:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]

EXPOSE 8000
