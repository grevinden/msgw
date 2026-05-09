from __future__ import annotations

from contextlib import asynccontextmanager
from logging import getLogger , Logger , DEBUG
from pathlib import Path
from typing import Literal , Final , cast

from cashews import cache
from cashews.backends.interface import Backend
from cashews_mongo import MongoBackend , MongoClientSideBackend  # noqa
from fastapi import FastAPI
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
		yield {
			'connections' : ConnectionManager ,
			'bucket'      : cache ,
		}
	finally :
		await ConnectionManager.close ( )
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
			(Path ( __file__ ).parent.parent.parent / 'README.md')
			.read_text ( encoding = 'utf-8-sig' ).strip ( )
		] ,
	) ,
)


@app.exception_handler ( Exception )
async def exc ( *args , **kwargs ) -> None :
	pass


async def update_bucket (
		b: Backend , t: Literal [ "notify" , "receipt" ] , k: str , v: str , l: PositiveInt = Settings.cache_ttl
) -> str | None :  #
	await b.set ( key = k , value = v , expire = l )
	return cast ( str | None , b.get ( key = k , default = None ) )


async def send_pending_messages ( w: WebSocket , b: Backend ) -> None :
	async for k in b.scan ( "*" , batch_size = Settings.cache_batch_size ) :
		if t := await b.get ( key = k , default = None ) :
			await ws_send ( w , t )
