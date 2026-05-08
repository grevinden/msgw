# -*- coding: utf-8 -*-
"""
MessageCenter – WebSocket / HTTP шлюз с гарантированной доставкой.
(исправленная версия без ошибок sleep)
"""

from __future__ import annotations

from asyncio import wait_for , sleep
from contextlib import asynccontextmanager
from logging import getLogger , Logger , DEBUG
from os import environ
from pathlib import Path
from typing import Literal , Annotated , Any , Final , override
from weakref import WeakSet

import pydantic_core
# Используем кеширование от cashews, но конкретно нам нужен DiskCache бэкенд
from cashews import cache
from cashews.backends.diskcache import DiskCache
from cashews_mongo import MongoBackend
from click import get_app_dir
from fastapi import FastAPI
from pydantic import BaseModel , Field , computed_field , UrlConstraints , AnyUrl , UUID4
from pydantic_settings import BaseSettings
from starlette.requests import Request
from starlette.websockets import WebSocket , WebSocketDisconnect , WebSocketState
from ulid import ULID

# --------------------------------------------------------------------------
# 1. Константы и настройка логирования
# --------------------------------------------------------------------------

NAME: Final [ Literal [ "MSGW" ] ] = "MSGW"

logger: Final [ Logger ] = getLogger ( "uvicorn" )
if __debug__ :
	logger.setLevel ( DEBUG )


# --------------------------------------------------------------------------
# 2. Конфигурация через переменные окружения (pydantic-settings)
# --------------------------------------------------------------------------

class Settings (
	BaseSettings ,
	env_prefix = environ.get ( "APP" , NAME ).strip ( "_" ) + "_" ,
	case_sensitive = False ,
) :
	class CashewsUrl ( AnyUrl ) :
		_constraints = UrlConstraints ( allowed_schemes = [ "mem" , "redis" ] )

	cache: Annotated [ CashewsUrl , Field ( default = CashewsUrl ( "mem://" ) ) ]

	@override
	def model_post_init ( self , context: Any , / ) -> bool | None :
		try :
			return super ( ).model_post_init ( context )
		finally :
			cache.setup (
				settings_url = self.cache.unicode_string ( ) ,
				prefix = self.model_config [ "env_prefix" ] ,
			)
			logger.debug ( self )
			logger.info ( "Конфигурация загружена" )


# noinspection PyArgumentList
Settings = Settings ( )  # type: ignore


# --------------------------------------------------------------------------
# 3. Модели сообщений (Pydantic)
# --------------------------------------------------------------------------

# noinspection PyDataclass
class MessageSend ( BaseModel , frozen = True ) :
	typ: Literal [ "send" ]
	top: str
	mes: str


# noinspection PyDataclass
class MessageDone ( BaseModel , frozen = True ) :
	typ: Literal [ "done" ]


# noinspection PyDataclass
class MessageFail ( BaseModel , frozen = True ) :
	typ: Literal [ "fail" ]
	err: str | list [ str ]


# noinspection PyDataclass
class Message ( BaseModel , frozen = True ) :
	uuid: Annotated [ UUID4 , Field ( title = "UUID4" ) ]
	payload: Annotated [
		MessageSend | MessageDone | MessageFail ,
		Field ( discriminator = "typ" , title = "PAYLOAD" , description = "Содержимое пакета данных" ) ,
	]

	@computed_field ( title = "ULID" )
	@property
	def ulid ( self ) -> ULID :
		return ULID.from_uuid ( self.uuid )

	@computed_field ( title = "TYPE" )
	@property
	def typ ( self ) -> Literal [ "receipt" , "notify" , "unknown" ] :
		match self.payload.typ :
			case "send" :
				return "notify"
			case "done" | "fail" :
				return "receipt"
			case _ :
				return "unknown"

	@classmethod
	def from_json ( cls , text: str , / ) -> "Message | None" :
		try :
			return cls.model_validate_json ( text )
		except Exception as exc :
			try :
				data = pydantic_core.from_json ( text , allow_partial = True )
				uuid = data.get ( "uuid" ) or "00000000-0000-0000-0000-000000000000"
				return cls.model_validate (
					{
						"uuid"    : uuid ,
						"payload" : { "typ" : "fail" , "err" : str ( exc ) } ,
					} ,
				)
			except Exception :
				return None


# --------------------------------------------------------------------------
# 4. Менеджер соединений (пул WebSocket)
# --------------------------------------------------------------------------

class ConnectionManager :
	class Pool ( WeakSet [ WebSocket ] ) :
		async def broadcast ( self , message: Message ) -> None :
			data = message.model_dump_json ( )
			for conn in list ( self ) :
				try :
					await conn.send_text ( data )
				except Exception as e :
					logger.warning ( f"Ошибка отправки клиенту: {e}" )
					self.discard ( conn )

		async def close ( self ) -> None :
			for conn in list ( self ) :
				try :
					await conn.close ( )
				except Exception :
					self.discard ( conn )

	pool = Pool ( )

	@classmethod
	async def broadcast ( cls , message: Message ) -> Message :
		await cls.pool.broadcast ( message )
		return message

	@classmethod
	async def close ( cls ) -> None :
		await cls.pool.close ( )


# --------------------------------------------------------------------------
# 5. Lifespan – жизненный цикл приложения
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan ( app: FastAPI ) :
	bucket = DiskCache (
		directory = get_app_dir ( NAME ) ,
		shards = 1 ,
		timeout = 5 ,
	)
	await bucket.init ( )

	app.state.bucket = MongoBackend (
		uri = "mongodb://127.0.0.1:27017" , database = "cashews" , collection = "cache" )
	app.state.connections = ConnectionManager

	try :
		yield
	finally :
		await ConnectionManager.close ( )
		await bucket.close ( )


# --------------------------------------------------------------------------
# 6. FastAPI приложение
# --------------------------------------------------------------------------

app = FastAPI (
	lifespan = lifespan ,
	title = NAME ,
	version = 'made@getter.pro' ,
	redoc_url = '/' ,
	docs_url = '/docs' ,
	description = '\n'.join (
		[
			r'[reDoc](/) | [Swagger](/docs)' ,
			(Path ( __file__ ).parent.parent.parent / 'README.md').read_text ( encoding = 'utf-8-sig' ).strip ( )
		] ,
	) ,
)


async def update_bucket ( bucket: DiskCache , message: Message ) -> None :
	"""
	Обновляет хранилище в зависимости от типа сообщения.
	- notify → сохраняем JSON сообщения
	- receipt → удаляем по uuid
	"""
	match message.typ :
		case "notify" :
			await bucket.set ( key = message.uuid.hex , value = message.model_dump_json ( ) )
		case "receipt" :
			await bucket.delete ( key = message.uuid.hex )
		case _ :
			# unknown – ничего не делаем
			pass


async def send_pending_messages ( websocket: WebSocket , bucket: DiskCache ) -> bool :
	messages = [ ]
	try :
		async for key , msg_json in bucket.get_match ( "*" ) :
			logger.debug ( f"Отправка накопленного сообщения key={key}" )
			messages.append ( msg_json )
	except Exception as e :
		logger.exception ( "Ошибка при итерации по bucket.get_match" )
		try :
			await websocket.close ( )
		except :
			pass
		return False

	for msg_json in messages :
		try :
			await websocket.send_text ( msg_json )
		except WebSocketDisconnect :
			logger.info ( "Клиент отключился при отправке накопленных" )
			return False
		except Exception as e :
			logger.warning ( f"Не удалось отправить сообщение: {e}" )
	return True


# --------------------------------------------------------------------------
# 7. WebSocket эндпоинт
# --------------------------------------------------------------------------
@app.websocket ( "/{path:path}" )
async def exchange ( * , websocket: WebSocket ) -> None :
	await websocket.accept ( )
	bucket = app.state.bucket
	pool = app.state.connections.pool
	pool.add ( websocket )

	if not await send_pending_messages ( websocket , bucket ) :
		pool.discard ( websocket )
		return
	await sleep ( 0.1 )  # освобождение блокировки

	try :
		while True :
			try :
				raw_data = await wait_for ( websocket.receive_text ( ) , timeout = 0.1 )
			except TimeoutError :
				# Проверяем, жив ли сокет
				if websocket.client_state != WebSocketState.CONNECTED :
					break
				continue
			except (WebSocketDisconnect , RuntimeError) as e :
				# Любое закрытие соединения штатно завершает цикл
				if isinstance ( e , RuntimeError ) and "not connected" in str ( e ) :
					logger.debug ( "Соединение закрыто до начала чтения" )
				else :
					logger.debug ( "WebSocket отключён" )
				break
			except Exception as e :
				logger.exception ( f"Ошибка в цикле чтения: {e}" )
				break

			message = Message.from_json ( raw_data )
			if not message :
				continue
			await update_bucket ( bucket , message )
			await ConnectionManager.broadcast ( message )
	finally :
		pool.discard ( websocket )


# --------------------------------------------------------------------------
# 8. HTTP POST эндпоинт
# --------------------------------------------------------------------------

@app.post ( "/{path:path}" , status_code = 201 )
async def send ( request: Request , message: Message ) -> Message :
	bucket = request.app.state.bucket
	await update_bucket ( bucket , message )
	return await ConnectionManager.broadcast ( message )
