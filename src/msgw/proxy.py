import asyncio
import logging
import time

from fastapi_reverse_proxy import HealthChecker
from yarl import URL

logger = logging.getLogger(__name__)


class LenientHealthChecker(HealthChecker):
    """HealthChecker что проверяет только открытость порта (TCP)."""

    async def start(self):
        if not self._task:
            await self.check_all()
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            await self.check_all()
            await asyncio.sleep(self.interval)

    async def check_all(self):
        tasks = [self._check_target(host) for host in self.targets]
        await asyncio.gather(*tasks, return_exceptions=True)
        self.last_update = time.perf_counter()

    async def _check_target(self, host: str):
        start_time = time.perf_counter()
        try:
            url = URL(host)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(url.host, url.port or 443),
                timeout=self.timeout,
            )
            writer.close()
            await writer.wait_closed()
            self.status[host] = (time.perf_counter() - start_time) * 1000
        except Exception:
            self.status[host] = False


class HealthCheckerRegistry:
    """
    Динамический реестр HealthChecker'ов.
    Создаёт и хранит чекеры для каждого уникального хоста.
    """

    def __init__(self):
        self._checkers = {}
        self._lock = asyncio.Lock()

    @property
    def checkers(self) -> dict[URL, HealthChecker]:
        return self._checkers

    async def checker(self, host: URL) -> HealthChecker:  #

        if host not in self.checkers:
            self.checkers[host] = LenientHealthChecker(
                targets=[host.human_repr()],
                interval=3,
                timeout=2,
            )

            await self.checkers[host].start()

        return self.checkers[host]

    async def is_healthy(self, host: URL) -> bool:
        checker = await self.checker(host)
        key = host.human_repr().rstrip("/")
        status = checker.status.get(key)
        return status is not False


def _suppress_health_check_errors(loop, context):
    """Подавляет 'Future exception was never retrieved' от health-чека.

    asyncio.open_connection создаёт внутреннюю задачу для DNS, и если
    DNS падает (gaierror), исключение остаётся висячим. Это известный
    баг Python: https://github.com/python/cpython/issues/95243
    """
    exc = context.get("exception")
    if isinstance(exc, (OSError,)):
        err = exc.errno
        # gaierror: -2 Name does not resolve, -3 Try again, -5 No address
        if err in (-2, -3, -5):
            return
    # Всё остальное логируем как обычно
    logger.warning("Health check error: %s", context.get("message", "unknown"))


# Устанавливаем handler на уровне event loop
# (нужно сделать это один раз при импорте)
def setup_exception_handler():
    """Настроить подавление health-check ошибок."""
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_suppress_health_check_errors)
    except RuntimeError:
        # Нет активного loop — установим на следующий запуск
        pass


health_registry = HealthCheckerRegistry()
