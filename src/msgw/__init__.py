from asyncer import create_task_group
from cashews.backends.interface import Backend
from cashews_mongo import MongoBackend
from fastapi import BackgroundTasks
from fastapi.logger import logger
from starlette.requests import Request
from starlette.websockets import WebSocket , WebSocketDisconnect

from .core import app , send_pending_messages , update_bucket
from .model import Message
from .ws import ConnectionManager , ws_conn


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


@app.post ( "/{path:path}" , status_code = 201 )
async def send ( r: Request , m: Message , t: BackgroundTasks ) -> Message :
	t.add_task ( process_message , m , r.state.bucket )
	return m
