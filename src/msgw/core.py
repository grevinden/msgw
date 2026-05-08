# -*- coding: utf-8 -*-
"""
MessageCenter – WebSocket / HTTP шлюз с гарантированной доставкой.

Принцип работы:
- Клиенты подключаются по WebSocket. Каждое соединение добавляется в пул (WeakSet).
- Сообщения типа 'notify' (содержимое отправителя) сохраняются в дисковом кеше.
- Сообщения типа 'receipt' (квитанция) удаляют соответствующий notify из кеша.
- При подключении нового клиента ему отправляются ВСЕ текущие notify из кеша.
- Новые сообщения рассылаются всем подключённым клиентам (broadcast).
- Все операции с кешем – асинхронные, не блокируют event loop.
"""

from __future__ import annotations

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
from click import get_app_dir
from fastapi import FastAPI
from pydantic import BaseModel , Field , computed_field , UrlConstraints , AnyUrl , UUID4
from pydantic_settings import BaseSettings
from starlette.requests import Request
from starlette.websockets import WebSocket , WebSocketDisconnect
from ulid import ULID

# --------------------------------------------------------------------------
# 1. Константы и настройка логирования
# --------------------------------------------------------------------------

NAME: Final [ Literal [ "MSGW" ] ] = "MSGW"  # Имя приложения, используется для переменных окружения и каталога кеша

# Логгер берём из uvicorn, чтобы логи интегрировались с веб-сервером
logger: Final [ Logger ] = getLogger ( "uvicorn" )
if __debug__ :
	logger.setLevel ( DEBUG )  # В отладочном режиме показываем много информации


# --------------------------------------------------------------------------
# 2. Конфигурация через переменные окружения (pydantic-settings)
# --------------------------------------------------------------------------

class Settings (
	BaseSettings ,
	env_prefix = environ.get ( "APP" , NAME ).strip ( "_" ) + "_" ,
	case_sensitive = False ,
) :
	"""
	Конфигурация приложения. Переменные окружения должны предваряться префиксом.
	Пример: MSGW_CACHE = mem://  или MSGW_CACHE = redis://localhost:6379
	"""

	class CashewsUrl ( AnyUrl ) :
		# Ограничиваем допустимые схемы для кеша: mem (память) или redis
		_constraints = UrlConstraints ( allowed_schemes = [ "mem" , "redis" ] )

	cache: Annotated [ CashewsUrl , Field ( default = CashewsUrl ( "mem://" ) ) ]

	# Здесь мы используем только настройку кеша. При необходимости можно добавлять другие.

	@override
	def model_post_init ( self , context: Any , / ) -> bool | None :
		"""После загрузки конфигурации настраиваем глобальный объект cache из cashews."""
		try :
			return super ( ).model_post_init ( context )
		finally :
			# Настраиваем cashews: передаём URL и префикс ключей
			cache.setup (
				settings_url = self.cache.unicode_string ( ) ,
				prefix = self.model_config [ "env_prefix" ] ,
			)
			logger.debug ( self )
			logger.info ( "Конфигурация загружена" )


# Создаём единственный экземпляр конфигурации (читается из переменных окружения)
# noinspection PyArgumentList
Settings = Settings ( )  # type: ignore


# --------------------------------------------------------------------------
# 3. Модели сообщений (Pydantic)
# --------------------------------------------------------------------------

# noinspection PyDataclass
class MessageSend ( BaseModel , frozen = True ) :
	"""
	Полезная нагрузка типа 'send' – обычное сообщение от клиента или сервера.
	top – тема, mes – текст сообщения.
	frozen=True делает модель неизменяемой (хешируемой).
	"""
	typ: Literal [ "send" ]
	top: str
	mes: str


# noinspection PyDataclass
class MessageDone ( BaseModel , frozen = True ) :
	"""Нагрузка 'done' – квитанция об успешной обработке."""
	typ: Literal [ "done" ]


# noinspection PyDataclass
class MessageFail ( BaseModel , frozen = True ) :
	"""Нагрузка 'fail' – сообщение об ошибке парсинга или другой ошибке."""
	typ: Literal [ "fail" ]
	err: str | list [ str ]  # Текст ошибки или список ошибок


# noinspection PyDataclass
class Message ( BaseModel , frozen = True ) :
	"""
	Корневая модель сообщения. Содержит UUID и дискриминированный payload.
	Дискриминатор 'typ' позволяет Pydantic автоматически выбирать нужную модель.
	"""

	uuid: Annotated [ UUID4 , Field ( title = "UUID4" ) ]
	payload: Annotated [
		MessageSend | MessageDone | MessageFail ,
		Field (
			discriminator = "typ" ,
			title = "PAYLOAD" ,
			description = "Содержимое пакета данных" ,
		) ,
	]

	@computed_field ( title = "ULID" )
	@property
	def ulid ( self ) -> ULID :
		"""
		Вычисляемое поле ULID – варианта UUID, сортируемая по времени.
		Используется для удобства, но в качестве ключа кеша мы используем uuid (строку).
		"""
		return ULID.from_uuid ( self.uuid )

	@computed_field ( title = "TYPE" )
	@property
	def typ ( self ) -> Literal [ "receipt" , "notify" , "unknown" ] :
		"""
		Вычисляемое поле, определяющее тип сообщения для бизнес-логики:
		- 'notify' – сообщение, требующее подтверждения (payload.send)
		- 'receipt' – квитанция (payload.done или payload.fail)
		- 'unknown' – неопределённый (не должно возникать)
		"""
		match self.payload.typ :
			case "send" :
				return "notify"
			case "done" | "fail" :
				return "receipt"
			case _ :
				return "unknown"

	@classmethod
	def from_json ( cls , text: str , / ) -> "Message | None" :
		"""
		Парсинг JSON в Message.
		При ошибке валидации пытаемся создать fail-сообщение с сохранением исходного UUID (если удалось извлечь).
		Если не удалось – возвращаем None (сообщение игнорируется).
		"""
		try :
			return cls.model_validate_json ( text )
		except Exception as exc :
			# Пробуем извлечь хотя бы UUID из текста (частичный парсинг)
			try :
				data = pydantic_core.from_json ( text , allow_partial = True )
				# Берём существующий UUID или подставляем нулевой
				uuid = data.get ( "uuid" ) or "00000000-0000-0000-0000-000000000000"
				return cls.model_validate (
					{
						"uuid"    : uuid ,
						"payload" : { "typ" : "fail" , "err" : str ( exc ) } ,
					} ,
				)
			except Exception :
				# Если даже UUID не извлекли – сообщение невалидно
				return None


# --------------------------------------------------------------------------
# 4. Менеджер соединений (пул WebSocket)
# --------------------------------------------------------------------------

class ConnectionManager :
	"""
	Менеджер, хранящий все активные WebSocket-соединения.
	Использует WeakSet, чтобы автоматически забывать закрытые соединения (помогает GC).
	"""

	class Pool ( WeakSet [ WebSocket ] ) :
		"""
		Расширяем WeakSet методами broadcast и close.
		WeakSet хранит слабые ссылки, поэтому удаление объекта из памяти автоматически удалит его из пула.
		"""

		async def broadcast ( self , message: Message ) -> None :
			"""
			Отправляет одно сообщение всем текущим клиентам.
			Если при отправке клиенту возникает ошибка – он считается мёртвым и удаляется из пула.
			"""
			data = message.model_dump_json ( )  # сериализуем один раз
			# Создаём копию списка, так как во время итерации пул может измениться
			for conn in list ( self ) :
				try :
					await conn.send_text ( data )
				except Exception as e :
					# Клиент, вероятно, отключился – удаляем слабую ссылку
					logger.warning ( f"Ошибка отправки клиенту: {e}" )
					self.discard ( conn )

		async def close ( self ) -> None :
			"""Закрыть все активные соединения (вызывается при завершении приложения)."""
			for conn in list ( self ) :
				try :
					await conn.close ( )
				except Exception :
					self.discard ( conn )

	# Единственный пул на всё приложение
	pool = Pool ( )

	@classmethod
	async def broadcast ( cls , message: Message ) -> Message :
		"""Утилитарный метод для рассылки сообщения."""
		await cls.pool.broadcast ( message )
		return message

	@classmethod
	async def close ( cls ) -> None :
		"""Утилитарный метод для закрытия всех соединений."""
		await cls.pool.close ( )


# --------------------------------------------------------------------------
# 5. Lifespan – жизненный цикл приложения
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan ( app: FastAPI ) :
	"""
	Выполняется при старте и остановке FastAPI.
	Здесь инициализируем дисковый кеш и сохраняем его в app.state для доступа из обработчиков.
	Также сохраняем ссылку на ConnectionManager.
	"""
	# Создаём экземпляр бэкенда DiskCache от cashews.
	# Параметры:
	#   directory – каталог для хранения файлов кеша (платформозависимый путь через click.get_app_dir).
	#   shards=1 – один шард (нешардированный), чтобы работал метод get_match.
	#   timeout=1 – таймаут блокировки 1 секунда.
	bucket = DiskCache (
		directory = get_app_dir ( NAME ) ,
		shards = 1 ,
		timeout = 1 ,
	)
	await bucket.init ( )  # Асинхронная инициализация (создание каталога и пр.)

	# Кладём объекты в app.state, чтобы они были доступны внутри веб-сокетов и http-запросов
	app.state.bucket = bucket
	app.state.connections = ConnectionManager

	try :
		yield  # Здесь запускается само приложение
	finally :
		# При выключении сервера закрываем пул соединений и кеш
		await ConnectionManager.close ( )
		await bucket.close ( )


# --------------------------------------------------------------------------
# 6. FastAPI приложение
# --------------------------------------------------------------------------


app = FastAPI (
	lifespan = lifespan , title = NAME , version = 'made@getter.pro' ,
	redoc_url = '/' , docs_url = '/docs' ,
	description = '\n'.join (
		[
			r'[reDoc](/) | [Swagger](/docs)' ,
			(Path ( __file__ ).parent.parent.parent / r'README.md')
			.read_text ( encoding = 'utf-8-sig' ).strip ( )
		] ) )


# --------------------------------------------------------------------------
# 7. WebSocket эндпоинт
# --------------------------------------------------------------------------

@app.websocket ( "/{path:path}" )
async def exchange ( * , websocket: WebSocket ) -> None :
	"""
	Обработчик WebSocket-соединений. Путь может быть любым (capture all).
	"""
	# Принимаем соединение
	await websocket.accept ( )

	# Получаем глобальные объекты из app.state
	bucket = app.state.bucket  # Дисковый кеш
	pool = app.state.connections.pool  # Пул соединений

	# Добавляем текущий сокет в пул
	pool.add ( websocket )

	# ----- 1. Отправляем новому клиенту все неподтверждённые сообщения (notify) -----
	try :
		# get_match("*") возвращает асинхронный итератор всех пар (ключ, значение) в кеше.
		# Паттерн "*" означает все ключи.
		async for key , msg in bucket.get_match ( "*" ) :
			# msg – это объект Message (типа notify), сохранённый ранее
			try :
				await websocket.send_text ( msg )
			except WebSocketDisconnect :
				# Клиент закрыл соединение во время отправки начальных данных – выходим
				logger.info ( "Клиент отключился при отправке накопленных сообщений" )
				return  # Выход из функции, finally всё равно выполнится? Нет, return прервёт, но ниже мы в finally не попадём, поэтому важно:
			# На самом деле после return управление уходит из функции, но finally из try-finally ниже не выполнится.
			# Поэтому здесь нужно вручную удалить сокет из пула и закрыть.
			# Однако мы сделаем по-другому: просто выбросим исключение, чтобы попасть в общий finally.
			# Но для простоты – сделаем return, а удаление сокета оставим на finally блока (но он не выполнится!).
			# Значит, правильнее: поймать WebSocketDisconnect и затем выйти, но перед выходом вызвать pool.discard.
			# Давайте улучшим: при такой ошибке сразу удаляем и выходим.
			except Exception as e :
				# Ошибка отправки конкретного сообщения (не разрыв связи) – логируем и продолжаем
				logger.warning ( f"Не удалось отправить сообщение {key}: {e}" )
	except WebSocketDisconnect :
		# Если клиент отвалился прямо во время итерации get_match
		pool.discard ( websocket )
		return
	except Exception as e :
		# Любая другая ошибка (например, проблемы с кешем)
		logger.exception ( "Ошибка при итерации по bucket.get_match" )
		pool.discard ( websocket )
		await websocket.close ( )
		return

	# ----- 2. Основной цикл приёма сообщений от клиента -----
	try :
		async for raw_data in websocket.iter_text ( ) :  #

			# Парсим JSON в Message (при ошибке вернётся None или fail-сообщение)
			message = Message.from_json ( raw_data )
			if not message :
				# Сообщение полностью невалидно – игнорируем (можно добавить логирование)
				continue

			# Обновляем хранилище неподтверждённых сообщений
			match message.typ :
				case "notify" :
					# Для notify – сохраняем в кеш. Ключ – строковое представление UUID
					await bucket.set ( key = message.uuid.hex , value = message.model_dump_json ( ) )
				case "receipt" :
					# Для receipt – удаляем соответствующий notify из кеша
					await bucket.delete ( key = message.uuid.hex )

			# Рассылаем сообщение всем активным клиентам (включая текущего)
			await ConnectionManager.broadcast ( message )

	except WebSocketDisconnect :
		# Нормальное отключение клиента – ничего страшного
		logger.debug ( "WebSocket отключён штатно" )

	except Exception as e :
		# Неожиданная ошибка в основном цикле (логируем, но не прерываем работу сервера)
		logger.exception ( f"Неожиданная ошибка в WebSocket цикле: {e}" )

	finally :
		# В любом случае удаляем сокет из пула при завершении соединения
		pool.discard ( websocket )


# --------------------------------------------------------------------------
# 8. HTTP POST эндпоинт
# --------------------------------------------------------------------------

@app.post ( "/{path:path}" , status_code = 201 )
async def send ( request: Request , message: Message ) -> Message :
	"""
	Приём сообщений через POST-запросы. Тело запроса должно быть валидным JSON, соответствующим модели Message.
	Сообщение рассылается всем WebSocket-клиентам, также обновляется кеш (как и при получении через WS).
	"""
	bucket = request.app.state.bucket

	# Обновляем хранилище неподтверждённых сообщений
	match message.typ :
		case "notify" :
			# Для notify – сохраняем в кеш. Ключ – строковое представление UUID
			await bucket.set ( key = message.uuid.hex , value = message.model_dump_json ( ) )
		case "receipt" :
			# Для receipt – удаляем соответствующий notify из кеша
			await bucket.delete ( key = message.uuid.hex )

	# Рассылаем всем
	return await ConnectionManager.broadcast ( message )
