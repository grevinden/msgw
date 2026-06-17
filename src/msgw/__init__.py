from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator

import yarl
from asyncer import create_task_group
from cashews.backends.interface import Backend
from cashews_mongo import MongoBackend
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.logger import logger
from fastapi_reverse_proxy import proxy_pass
from fastapi_reverse_proxy.proxy_httpx import Proxy
from httpx import ConnectError
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import header_system_id, settings
from .core import QueryFreeHttpUrl, app, send_pending_messages, update_bucket
from .model import Message
from .proxy import health_registry
from .ws import ConnectionManager


# noinspection PyUnusedLocal
@app.exception_handler(ConnectError)
async def exc_httpx(request: Request, exc: ConnectError):
    msg = str(exc)
    # Делаем ошибку понятной для пользователя
    if "Name does not resolve" in msg or "DNS" in msg:
        detail = f"Upstream DNS error: {msg}"
    elif "Connection refused" in msg:
        detail = f"Upstream connection refused: {msg}"
    elif "timed out" in msg or "Timeout" in msg:
        detail = f"Upstream timeout: {msg}"
    else:
        detail = f"Upstream connection error: {msg}"
    return PlainTextResponse(
        status_code=502, content=detail, headers=header_system_id
    )


# noinspection PyUnusedLocal
@app.exception_handler(RequestValidationError)
async def err_422(request: Request, exc: RequestValidationError):
    return PlainTextResponse(
        status_code=422, content=str(exc), headers=header_system_id
    )


# noinspection PyUnusedLocal
@app.exception_handler(Exception)
async def exc_default(request: Request, exc: Exception):
    return PlainTextResponse(
        status_code=500, content=str(exc), headers=header_system_id
    )


async def exchange_prepare(w: WebSocket, /) -> MongoBackend:
    await w.accept()
    w.state.connections.pool.add(w)
    return w.state.bucket


async def process_message(m: Message, b: Backend, /) -> None:
    # noinspection PyTypeChecker
    await ConnectionManager.broadcast(
        await update_bucket(b, m.typ, m.uuid.hex, m.model_dump_json(), m.ttl)
    )


async def process_exchange(w: WebSocket, b: Backend, /) -> None:
    async for t in w.iter_text():
        if t and (m := Message.from_json(t)):
            await process_message(m, b)


@app.websocket("/{path:path}")
async def exchange(*, w: WebSocket) -> None:
    b = await exchange_prepare(w)
    try:
        async with create_task_group() as g:
            g.soonify(send_pending_messages)(w, b)
            g.soonify(process_exchange)(w, b)

    except* (WebSocketDisconnect, RuntimeError) as e:
        for e in e.exceptions:
            logger.debug(f"{type(e)}: {e}")


@app.api_route("/{path:path}", methods=["QUERY"])
async def send(r: Request, m: Message, t: BackgroundTasks) -> Message:
    t.add_task(process_message, m, r.state.bucket)
    return m


# noinspection PyUnusedLocal
@asynccontextmanager
async def proxy_lifespan(a: FastAPI) -> AsyncGenerator[dict[str, Any], None]:  #

    async with Proxy(app) as p:
        yield {"proxy": p}


if settings.ecies.key:
    from .ecies import decrypt_bytes

    proxy_router = APIRouter(lifespan=proxy_lifespan)

    @proxy_router.post(
        "/{path:path}",
        response_class=Response,
        response_model=None,
        summary="Proxy pass",
        description=(
            "Расшифровывает тело запроса (ECIES) и пересылает на upstream.\n\n"
            "**Health-чек:** TCP-проверка порта upstream. \n"
            "- Первый запрос к новому хосту включает проверку (до 2 сек).\n"
            "- Фоновая проверка каждые 3 сек.\n"
            "- Если порт недоступен — 502.\n\n"
            "**Upstream:** указывается в query-параметре `?upstream=...`.\n"
            "**Тело:** зашифрованные фрагменты в формате `{{base64url_данные}}`."
        ),
    )
    async def proxy_post(
        req: Request, upstream: Annotated[QueryFreeHttpUrl, Query()]
    ):  #

        url = yarl.URL(upstream.unicode_string())
        origin = url.origin()

        if not await health_registry.is_healthy(origin):
            raise HTTPException(
                status_code=502,
                detail=f"Backend not healthy: {origin.human_repr()}",
                headers={
                    "Retry-After": "3",
                    "X-Upstream-Status": "unhealthy",
                },
            )
        else:
            req.url.remove_query_params("upstream")
            body = decrypt_bytes(await req.body())[0]
            headers = req.headers.mutablecopy()
            del headers["content-length"]
            result = await proxy_pass(
                override_body=body.get_secret_value(),
                request=req,
                path=url.path,
                override_headers=dict(headers) | {"X-System-ID": "upstream"},
                host=url.origin().human_repr(),
            )
            result.headers.update({"X-System-ID": "upstream"})
            result.headers.update({"X-Upstream-Status": "healthy"})
            return result

    app.include_router(proxy_router)
