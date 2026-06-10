from __future__ import annotations

from asyncio import shield
from contextlib import asynccontextmanager
from logging import getLogger , Logger , DEBUG
from typing import Literal , Final , cast , Any

from asyncer import create_task_group
from cashews import cache
from cashews.backends.interface import Backend
from cashews.exceptions import CacheError
from cashews_mongo import MongoBackend , MongoClientSideBackend  # noqa
from fastapi import FastAPI
from pydantic import PositiveInt , HttpUrl
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema , PydanticCustomError
from starlette.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocket

from .config import settings , header_system_id
from .environ import NAME
from .ws import ConnectionManager , ws_send

logger: Final [ Logger ] = getLogger ( "uvicorn" )

class QueryFreeHttpUrl ( HttpUrl ) :
	"""URL тип, запрещающий query-параметры

	Примеры валидных URL:
	- https://example.com/api
	- http://localhost:8000/path/to/resource

	Невалидные URL:
	- https://example.com/api?foo=bar
	- http://localhost:8000/path?param=value
	"""

	@classmethod
	def __get_pydantic_core_schema__ (
		cls , source_type: Any , handler
	) -> core_schema.CoreSchema :
		schema = HttpUrl.__get_pydantic_core_schema__ ( source_type , handler )

		validated_schema = core_schema.no_info_after_validator_function (
			cls._validate_no_query ,
			schema ,
		)

		return validated_schema

	@classmethod
	def _validate_no_query ( cls , url: HttpUrl ) -> HttpUrl :
		if url.query :
			raise PydanticCustomError (
				'query_forbidden' ,
				'Query parameters are not allowed: {query}' ,
				{ 'query' : url.query } ,
			)
		return url

	@classmethod
	def __get_pydantic_json_schema__ (
		cls , schema: core_schema.CoreSchema , handler
	) -> JsonSchemaValue :
		json_schema = handler ( schema )

		# Расширенное описание для Swagger/OpenAPI
		json_schema.update ( {
			'title'       : 'Query-Free URL' ,
			'description' : (
				'**IMPORTANT**: This URL must NOT contain query parameters.\n\n'
				'Valid: `https://api.example.com/v1/users`\n\n'
				'Invalid: `https://api.example.com/v1/users?page=1`\n\n'
				'Query parameters should be passed via request body or headers.'
			) ,
			'examples'    : [
				'https://api.example.com/v1/users' ,
				'https://example.com/resource/123' ,
			] ,
			'format'      : 'uri' ,
		} )

		return json_schema


# noinspection PyUnusedLocal
@asynccontextmanager
async def lifespan ( a: FastAPI ) :
	await cache.init ( )

	try :
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
	headers = header_system_id ,
	description = '\n'.join (
		[
			r'[reDoc](/) | [Swagger](/docs)' ,
			(settings.path.root / 'README.md')
			.read_text ( encoding = 'utf-8-sig' ).strip ( )
		] ,
	) ,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# noinspection PyUnusedLocal
async def update_bucket (
	b: Backend , t: Literal [ "notify" , "receipt" ] , k: str , v: str , l: PositiveInt = settings.cache.ttl
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
		async for k in b.scan ( "*" , batch_size = 100 ) :
			tg.soonify ( shield ) ( send_pending_message ( w , b , k ) )
