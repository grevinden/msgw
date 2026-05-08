#!/usr/bin/env python3
"""
Групповой тест MessageCenter на полной мощности.

Использование:
    python group_test.py --senders 20 --receivers 20 --max-messages 1000 --verbose
Остановка: Ctrl+C
"""

import asyncio
import json
import uuid
from typing import Set, List, Optional
from collections import defaultdict

import typer
import websockets
from rich.logging import RichHandler
import logging

app = typer.Typer(help="Групповой тест MessageCenter (бесконечная нагрузка)")

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
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
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
                    resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(resp)
                    if data.get("uuid") == uid and data.get("typ") == "receipt":
                        break
            logger.info(f"🧹 Очищено {len(pending)} старых сообщений")
            return len(pending)
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        return 0

async def sender(
    sender_id: int,
    ws_url: str,
    logger: logging.Logger,
    stats: dict,
    max_messages: int,
    stop_event: asyncio.Event,
) -> None:
    """Отправитель – шлёт send и ждёт receipt (до stop_event или лимита)."""
    sent = 0
    confirmed = 0
    try:
        async with websockets.connect(ws_url) as ws:
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
                        resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(resp)
                        if data.get("uuid") == msg_uuid and data.get("typ") == "receipt":
                            confirmed += 1
                            logger.debug(f"[S{sender_id}] ← receipt {msg_uuid[:8]}")
                            break
                    except asyncio.TimeoutError:
                        logger.warning(f"[S{sender_id}] Таймаут для {msg_uuid[:8]}")
                        break
    except Exception as e:
        logger.error(f"[S{sender_id}] Ошибка: {e}")
    finally:
        stats[sender_id] = (sent, confirmed)

async def receiver(
    receiver_id: int,
    ws_url: str,
    logger: logging.Logger,
    handled_uuids: Set[str],
    stop_event: asyncio.Event,
) -> None:
    """Получатель – слушает notify и отвечает done."""
    processed = 0
    try:
        async with websockets.connect(ws_url) as ws:
            # Пропускаем возможные старые сообщения (после очистки их нет)
            await asyncio.sleep(0.5)
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
        logger.error(f"[R{receiver_id}] Ошибка: {e}")
    finally:
        logger.debug(f"[R{receiver_id}] Обработано: {processed}")

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

    # Очистка перед тестом
    await clear_server(ws_url, logger)

    stop_event = asyncio.Event()
    stats = {}
    handled_uuids = set()

    # Запуск получателей
    receiver_tasks = [
        asyncio.create_task(receiver(i, ws_url, logger, handled_uuids, stop_event))
        for i in range(receivers)
    ]
    await asyncio.sleep(0.5)  # даём получателям подключиться

    # Запуск отправителей
    sender_tasks = [
        asyncio.create_task(sender(i, ws_url, logger, stats, max_messages, stop_event))
        for i in range(senders)
    ]

    try:
        # Ждём либо завершения отправителей (если лимит достигнут), либо остановки
        await asyncio.gather(*sender_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Возникает при Ctrl+C (через asyncio.run)
        logger.info("⚠️ Получен сигнал остановки, завершаем...")
    finally:
        # Останавливаем получателей
        stop_event.set()
        await asyncio.gather(*receiver_tasks, return_exceptions=True)

    # Статистика
    total_sent = sum(s[0] for s in stats.values())
    total_confirmed = sum(s[1] for s in stats.values())
    unique_handled = len(handled_uuids)

    print("\n" + "=" * 60)
    logger.info(f"📊 СТАТИСТИКА:")
    logger.info(f"   Отправлено send: {total_sent}")
    logger.info(f"   Получено receipt (подтверждено): {total_confirmed}")
    logger.info(f"   Обработано notify получателями: {unique_handled}")
    if total_sent > 0:
        logger.info(f"   Доля подтверждённых: {total_confirmed / total_sent * 100:.2f}%")
    logger.info(f"✅ Всего отправителей: {len(stats)}, получателей: {receivers}")

    # Очистка после теста
    logger.info("Очистка сервера после теста...")
    await clear_server(ws_url, logger)

    if total_confirmed == total_sent and total_sent == unique_handled and total_sent > 0:
        logger.info("🎉 ТЕСТ ПРОЙДЕН УСПЕШНО")
        return 0
    else:
        if total_sent == 0:
            logger.warning("⚠️ Не было отправлено ни одного сообщения")
        else:
            logger.error("❌ ТЕСТ ЗАВЕРШИЛСЯ С ОШИБКАМИ")
        return 1

@app.command()
def main(
    senders: int = typer.Option(2, "--senders", "-s", help="Количество отправителей"),
    receivers: int = typer.Option(2, "--receivers", "-r", help="Количество получателей"),
    url: str = typer.Option("ws://localhost:8000/test", "--url", "-u", help="WebSocket URL"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный вывод"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Отладочный вывод"),
    max_messages: int = typer.Option(-1, "--max-messages", "-M", help="Максимум сообщений на отправителя (-1 = без лимита)"),
) -> None:
    """
    Групповой тест на полной мощности. Остановка: Ctrl+C.
    """
    exit_code = asyncio.run(async_main(senders, receivers, url, verbose, debug, max_messages))
    raise typer.Exit(code=exit_code)

if __name__ == "__main__":
    app()
