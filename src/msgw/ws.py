from asyncio import sleep , shield
from typing import Awaitable
from weakref import WeakSet

from asyncer import create_task_group
from pydantic import validate_call , ConfigDict
from starlette.websockets import WebSocket , WebSocketState


@validate_call ( config = ConfigDict ( arbitrary_types_allowed = True ) )
async def ws_send ( w: WebSocket , t: str , / ) -> None :
	if ws_conn ( w ) :
		await w.send_text ( t )


def ws_conn ( w: WebSocket , / ) -> bool :
	return w.application_state == WebSocketState.CONNECTED


class ConnectionManager :
	class Pool ( WeakSet [ WebSocket ] ) :
		async def broadcast ( self , m: str ) -> None :
			async with create_task_group ( ) as tg :
				for w in list ( self ) :
					tg.soonify ( shield ) ( ws_send ( w , m ) )

	pool = Pool ( )

	@classmethod
	@validate_call
	def broadcast ( cls , m: str ) -> Awaitable[None] :
		return cls.pool.broadcast ( m )
