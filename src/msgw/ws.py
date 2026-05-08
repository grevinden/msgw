from asyncio import sleep
from weakref import WeakSet

from starlette.websockets import WebSocket , WebSocketState


async def ws_send ( w: WebSocket , t: str , / ) -> None :
	await sleep ( .3 )
	if ws_conn ( w ) :
		await w.send_text ( t )


def ws_conn ( w: WebSocket , / ) -> bool :
	return w.application_state == WebSocketState.CONNECTED


class ConnectionManager :
	class Pool ( WeakSet [ WebSocket ] ) :
		async def broadcast ( self , m: str ) -> None :
			for w in list ( self ) :
				await ws_send ( w , m )

		async def close ( self ) -> None :
			for conn in list ( self ) :
				try :
					await conn.close ( )
				except Exception :
					self.discard ( conn )

	pool = Pool ( )

	@classmethod
	async def broadcast ( cls , m: str ) -> None :
		await cls.pool.broadcast ( m )

	@classmethod
	async def close ( cls ) -> None :
		await cls.pool.close ( )
