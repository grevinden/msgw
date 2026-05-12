from __future__ import annotations

from asyncio import shield
from contextlib import asynccontextmanager
from logging import getLogger , Logger , DEBUG
from typing import Literal , Final , cast

from asyncer import create_task_group
from cashews import cache
from cashews.backends.interface import Backend
from cashews.exceptions import CacheError
from cashews_mongo import MongoBackend , MongoClientSideBackend  # noqa
from fastapi import FastAPI
from fastapi_reverse_proxy.proxy_httpx import Proxy
from pydantic import PositiveInt
from starlette.websockets import WebSocket

from .settings import NAME , Settings
from .ws import ConnectionManager , ws_send

logger: Final [ Logger ] = getLogger ( "uvicorn" )
if __debug__ :
	logger.setLevel ( DEBUG )


@asynccontextmanager
async def lifespan ( app: FastAPI ) :
	await cache.init ( )

	try :
		async with Proxy ( app ) :
			yield {
				'connections' : ConnectionManager ,
				'bucket'      : cache ,
			}
	finally :
		await cache.close ( )


app = FastAPI (
	lifespan = lifespan ,
	title = NAME ,
	version = 'made@getter.pro' ,
	redoc_url = '/' ,
	docs_url = '/docs' ,
	description = '\n'.join (
		[
			r'[reDoc](/) | [Swagger](/docs)' ,
			(Settings.path_root / 'README.md')
			.read_text ( encoding = 'utf-8-sig' ).strip ( )
		] ,
	) ,
)


async def update_bucket (
		b: Backend , t: Literal [ "notify" , "receipt" ] , k: str , v: str , l: PositiveInt = Settings.cache_ttl
) -> str | None :  #
	await b.set ( key = k , value = v , expire = l )
	if result := cast ( str | None , await b.get ( key = k , default = None ) ) :
		return result
	else :
		raise CacheError


async def send_pending_message ( w: WebSocket , b: Backend , k: str ) -> None :
	if t := await b.get ( key = k , default = None ) :
		await ws_send ( w , t )


async def send_pending_messages ( w: WebSocket , b: Backend ) -> None :
	async with create_task_group ( ) as tg :
		async for k in b.scan ( "*" , batch_size = Settings.cache_batch_size ) :
			tg.soonify ( shield ) ( send_pending_message ( w , b , k ) )
