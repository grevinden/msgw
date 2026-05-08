#!/usr/bin/env python3
"""
Групповой тест MessageCenter (помогает выявить ошибки)
"""

import asyncio
import json
import uuid
import logging
from typing import Set, List

import typer
import websockets
from rich.logging import RichHandler

app = typer.Typer(help="Групповой тест MessageCenter (диагностика ошибок)")

def setup_logging(verbose: bool, debug: bool) -> logging.Logger:
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)]
    )
    return logging.getLogger("grouptest")

async def clear_server(ws_url: str, logger: logging.Logger) -> int:
    """Полная очистка сервера от старых сообщений."""
    try:
        async with websockets.connect(ws_url) as ws:
            pending = []
            # Увеличиваем таймаут сбора сообщений до 2 секунд
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    data = json.loads(msg)
                    if data.get("typ") == "notify":
                        pending.append(data["uuid"])
                except asyncio.TimeoutError:
                    break
            if not pending:
                return 0
            for uid in pending:
                fail_msg = {"uuid": uid, "payload": {"typ": "fail", "err": "cleared"}}
                await ws.send(json.dumps(fail_msg))
                while True:
                    resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(resp)
                    if data.get("uuid") == uid and data.get("typ") == "receipt":
                        break
            logger.info(f"🧹 Очищено {len(pending)} старых сообщений")
            return len(pending)
    except Exception as e:
        logger.error(f"Ошибка очистки: {type(e).__name__}: {e}", exc_info=True)
        return 0

async def sender(
    sender_id: int,
    ws_url: str,
    logger: logging.Logger,
    stats: dict,
    max_messages: int,
    stop_event: asyncio.Event,
) -> None:
    sent = 0
    confirmed = 0
    try:
        logger.debug(f"[S{sender_id}] Подключение...")
        async with websockets.connect(ws_url) as ws:
            logger.debug(f"[S{sender_id}] Подключено")
            while not stop_event.is_set():
                if max_messages > 0 and sent >= max_messages:
                    break
                msg_uuid = str(uuid.uuid4())
                send_msg = {
                    "uuid": msg_uuid,
                    "payload": {"typ": "send", "top": f"sender_{sender_id}", "mes": f"Msg {sent}"}
                }
                logger.debug(f"[S{sender_id}] → send {msg_uuid[:8]}")
                await ws.send(json.dumps(send_msg))
                sent += 1

                while not stop_event.is_set():
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        data = json.loads(resp)
                        if data.get("uuid") == msg_uuid and data.get("typ") == "receipt":
                            confirmed += 1
                            logger.debug(f"[S{sender_id}] ← receipt {msg_uuid[:8]}")
                            break
                    except asyncio.TimeoutError:
                        logger.warning(f"[S{sender_id}] Таймаут receipt для {msg_uuid[:8]}")
                        break
        stats[sender_id] = (sent, confirmed)
    except Exception as e:
        logger.error(f"[S{sender_id}] Критическая ошибка: {type(e).__name__}: {e}", exc_info=True)
        stats[sender_id] = (0, 0)

async def receiver(
    receiver_id: int,
    ws_url: str,
    logger: logging.Logger,
    handled_uuids: Set[str],
    stop_event: asyncio.Event,
) -> None:
    processed = 0
    try:
        async with websockets.connect(ws_url) as ws:
            await asyncio.sleep(0.5)  # пропускаем возможные остатки
            while not stop_event.is_set():
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(resp)
                    if data.get("typ") == "notify":
                        uid = data["uuid"]
                        if uid not in handled_uuids:
                            handled_uuids.add(uid)
                            processed += 1
                            logger.debug(f"[R{receiver_id}] ← notify {uid[:8]}")
                            done_msg = {"uuid": uid, "payload": {"typ": "done"}}
                            await ws.send(json.dumps(done_msg))
                            logger.debug(f"[R{receiver_id}] → done {uid[:8]}")
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    break
    except Exception as e:
        logger.error(f"[R{receiver_id}] Ошибка: {type(e).__name__}: {e}", exc_info=True)

async def async_main(
    senders: int,
    receivers: int,
    ws_url: str,
    verbose: bool,
    debug: bool,
    max_messages: int,
) -> int:
    logger = setup_logging(verbose, debug)
    logger.info("🚀 Групповой тест MessageCenter")
    logger.info(f"   Отправителей: {senders}, получателей: {receivers}")
    if max_messages > 0:
        logger.info(f"   Лимит сообщений на отправителя: {max_messages}")
    else:
        logger.info("   Будет работать до остановки (Ctrl+C)")

    # Гарантированная очистка перед тестом
    logger.info("Очистка сервера перед тестом...")
    cleared = await clear_server(ws_url, logger)
    while cleared > 0:
        logger.info(f"Повторная очистка (осталось {cleared})...")
        await asyncio.sleep(1)
        cleared = await clear_server(ws_url, logger)

    stop_event = asyncio.Event()
    stats = {}
    handled_uuids = set()

    # Запуск получателей
    receiver_tasks = [
        asyncio.create_task(receiver(i, ws_url, logger, handled_uuids, stop_event))
        for i in range(receivers)
    ]
    await asyncio.sleep(0.5)

    # Запуск отправителей
    sender_tasks = [
        asyncio.create_task(sender(i, ws_url, logger, stats, max_messages, stop_event))
        for i in range(senders)
    ]

    try:
        await asyncio.gather(*sender_tasks)
    except asyncio.CancelledError:
        logger.info("⚠️ Тест прерван пользователем")
    finally:
        stop_event.set()
        # Даём получателям завершить
        await asyncio.sleep(1)
        await asyncio.gather(*receiver_tasks, return_exceptions=True)

    total_sent = sum(s[0] for s in stats.values())
    total_confirmed = sum(s[1] for s in stats.values())
    unique_handled = len(handled_uuids)

    print("\n" + "=" * 60)
    logger.info(f"📊 СТАТИСТИКА:")
    logger.info(f"   Отправлено send: {total_sent}")
    logger.info(f"   Получено receipt: {total_confirmed}")
    logger.info(f"   Обработано notify получателями: {unique_handled}")
    if total_sent > 0:
        logger.info(f"   Доля подтверждённых: {total_confirmed / total_sent * 100:.2f}%")
    logger.info(f"✅ Всего отправителей: {len(stats)}, получателей: {receivers}")

    extra = unique_handled - total_sent
    if extra > 0:
        logger.warning(f"⚠️ Обработано {extra} сообщений, которые не были отправлены в этом тесте (старые)")
    elif extra < 0:
        logger.error(f"❌ Не получили подтверждения для {-extra} сообщений")

    # Финальная очистка
    logger.info("Финальная очистка сервера...")
    await clear_server(ws_url, logger)

    if total_confirmed == total_sent and total_sent == unique_handled and total_sent > 0:
        logger.info("🎉 ТЕСТ ПРОЙДЕН УСПЕШНО")
        return 0
    else:
        logger.error("❌ ТЕСТ ЗАВЕРШИЛСЯ С ОШИБКАМИ")
        return 1

@app.command()
def main(
    senders: int = typer.Option(20, "--senders", "-s", help="Количество отправителей"),
    receivers: int = typer.Option(20, "--receivers", "-r", help="Количество получателей"),
    url: str = typer.Option("ws://localhost:8000/test", "--url", "-u", help="WebSocket URL"),
    verbose: bool = typer.Option(True, "--verbose", "-v", help="Подробный вывод"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Отладочный вывод"),
    max_messages: int = typer.Option(-1, "--max-messages", "-M", help="Лимит сообщений на отправителя (-1 = без лимита)"),
):
    exit_code = asyncio.run(async_main(senders, receivers, url, verbose, debug, max_messages))
    raise typer.Exit(code=exit_code)

if __name__ == "__main__":
    app()
