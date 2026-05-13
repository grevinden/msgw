import asyncio

import httpx
from fastapi_reverse_proxy import HealthChecker
from httpx import Timeout
from yarl import URL

from .config import settings


class HealthCheckerRegistry :
	"""
	Динамический реестр HealthChecker'ов.
	Создаёт и хранит чекеры для каждого уникального хоста.
	"""

	def __init__ ( self ) :

		self._checkers = { }
		self._lock = asyncio.Lock ( )
		self._client = httpx.AsyncClient (
			timeout = Timeout (settings.health_checker_timeout,
			                   connect = settings.health_checker_timeout ) )

	@property
	def checkers ( self ) -> dict [ URL , HealthChecker ] :
		return self._checkers

	async def checker ( self , host: URL ) -> HealthChecker :  #

		if host not in self.checkers :
			self.checkers [ host ] = HealthChecker (
				targets = [ host.human_repr ( ) ] ,
				interval = settings.health_checker_interval ,
				timeout = settings.health_checker_timeout ,
				httpx_client = self._client ,  # Передаём общий клиент
			)

			# Запускаем фоновую проверку
			await self.checkers [ host ].start ( )

		return self.checkers [ host ]

health_registry = HealthCheckerRegistry ( )
