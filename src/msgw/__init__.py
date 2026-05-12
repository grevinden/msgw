from contextlib import asynccontextmanager
from typing import Annotated , Any , AsyncGenerator

import yarl
from asyncer import create_task_group
from cashews.backends.interface import Backend
from cashews_mongo import MongoBackend
from fastapi import BackgroundTasks , Query , APIRouter , FastAPI
from fastapi.logger import logger
from fastapi_reverse_proxy import proxy_pass
from fastapi_reverse_proxy.proxy_httpx import Proxy
from httpx import ConnectError
from pydantic import HttpUrl
from starlette.requests import Request
from starlette.responses import Response , PlainTextResponse
from starlette.websockets import WebSocket , WebSocketDisconnect

from .core import app , send_pending_messages , update_bucket
from .model import Message
from .settings import Settings
from .ws import ConnectionManager , ws_conn

if Settings.ecies_key :
	from .ecies import decrypt_bytes


@app.exception_handler ( ConnectError )
async def exc_httpx ( request: Request , exc: ConnectError ) :
	return PlainTextResponse ( status_code = 502 , content = str ( exc ) )


@app.exception_handler ( Exception )
async def exc_httpx ( request: Request , exc: Exception ) :
	return PlainTextResponse ( status_code = 500 , content = str ( exc ) )


async def exchange_prepare ( w: WebSocket , / ) -> MongoBackend :
	await w.accept ( )
	w.state.connections.pool.add ( w )
	return w.state.bucket


async def process_message ( m: Message , b: Backend , / ) -> None :
	# noinspection PyTypeChecker
	await ConnectionManager.broadcast ( await update_bucket (
		b , m.typ , m.uuid.hex , m.model_dump_json ( ) , m.ttl ) )


async def process_exchange ( w: WebSocket , b: Backend , / ) -> None :
	async for t in w.iter_text ( ) :
		if t and (m := Message.from_json ( t )) :
			await process_message ( m , b )


@app.websocket ( "/{path:path}" )
async def exchange ( * , w: WebSocket ) -> None :
	b = await exchange_prepare ( w )
	try :
		async with create_task_group ( ) as g :
			g.soonify ( send_pending_messages ) ( w , b )
			g.soonify ( process_exchange ) ( w , b )

	except* (WebSocketDisconnect , RuntimeError) as e :
		for e in e.exceptions :
			logger.debug ( f"{type ( e )}: {e}" )


@app.api_route ( "/{path:path}" , methods = [ "QUERY" ] )
async def send ( r: Request , m: Message , t: BackgroundTasks ) -> Message :
	t.add_task ( process_message , m , r.state.bucket )
	return m


@asynccontextmanager
async def proxy_lifespan ( apps: FastAPI ) -> AsyncGenerator [ dict [ str , Any ] , Any ] :
	async with Proxy ( app ) as proxy :
		yield { "proxy" : proxy }


if Settings.ecies_key :
	proxy_router = APIRouter ( lifespan = proxy_lifespan )


	@proxy_router.post (
		"/{path:path}" , response_class = Response , response_model = None ,
		summary = "Proxy pass" ,
	)  #
	async def proxy_post ( req: Request , upstream: Annotated [ HttpUrl , Query ( ) ] ) :
		url = yarl.URL ( upstream.unicode_string ( ) )
		req._url = req.url.remove_query_params ( 'upstream' )

		body = decrypt_bytes ( await req.body ( ) ) [ 0 ]
		headers = dict ( req.headers )
		headers.pop ( "content-length" , None )
		return await proxy_pass (
			override_body = body.get_secret_value ( ) ,
			request = req , path = url.path , override_headers = headers ,
			host = url.origin ( ).human_repr ( ) , )


	app.include_router ( proxy_router )
