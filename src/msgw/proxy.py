import asyncio

import httpx
from fastapi_reverse_proxy import HealthChecker
from yarl import URL




class HealthCheckerRegistry :
	"""
	Динамический реестр HealthChecker'ов.
	Создаёт и хранит чекеры для каждого уникального хоста.
	"""

	def __init__ ( self , interval: int = 10 , timeout: int = 5 ) :
		self._checkers = { }
		self._interval = interval
		self._timeout = timeout
		self._lock = asyncio.Lock ( )
		self._client = httpx.AsyncClient ( timeout = timeout )

	@property
	def checkers ( self ) -> dict [ URL , HealthChecker ] :
		return self._checkers

	async def checker ( self , host: URL ) -> HealthChecker :#

			if host not in self.checkers :
				self.checkers [ host ] = HealthChecker (
					targets = [ host.human_repr ( ) ] ,
					interval = self._interval ,
					timeout = self._timeout ,
					httpx_client = self._client ,  # Передаём общий клиент
				)

				# Запускаем фоновую проверку
				await self.checkers [ host ].start ( )

			return self.checkers [ host ]


# Создаём глобальный реестр
health_registry = HealthCheckerRegistry (
	interval = 2 , timeout = 1 ,
)
