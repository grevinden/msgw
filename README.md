# MessageCenter

Сервис на FastAPI: приём/доставка сообщений (WebSocket, HTTP-QUERY) и хранение в Redis, а также reverse proxy с расшифровкой ECIES-вставок.  
Контейнер, только ENV-конфигурация.

---

## Архитектура

```
+-------------+      +-------------------+      +-------------------+
|  Клиенты    |      |  MessageCenter    |      |  Redis / Backend  |
| WS / HTTP   |<---->|  :8000            |<---->|                   |
+-------------+      +-------------------+      +-------------------+
        |                        |
        | Сообщения (WS, QUERY)  | Прокси (POST + upstream)
        +------------------------+
```

Два режима: **сообщения** (хранение + рассылка) и **прокси с расшифровкой** (при заданном ключе).

---

## Быстрый старт

```bash
docker network create msgw-net
docker run -d --name redis --network msgw-net redis --appendonly yes

# Без расшифровки
docker run -d --name msgw --network msgw-net -p 8000:8000 \
  -e MSGW_CACHE=redis://redis:6379/0 \
  ghcr.io/grevinden/msgw:latest

# С расшифровкой
docker run -d --name msgw --network msgw-net -p 8000:8000 \
  -e MSGW_CACHE=redis://redis:6379/0 \
  -e MSGW_ECIES_KEY="ваш_приватный_ключ_base64url" \
  ghcr.io/grevinden/msgw:latest
```

Открыты: `ws://<хост>:8000/<путь>`, `http://<хост>:8000/<путь>` (QUERY), `POST ...?upstream=...` (при ключе).

---

## Отправка сообщений через WebSocket

### Примеры подключения

**JavaScript**
```javascript
const ws = new WebSocket("ws://msgw:8000/chat/general");
ws.onopen = () => {
  ws.send(JSON.stringify({
    uuid: crypto.randomUUID(),
    ttl: 3600,
    payload: { typ: "send", top: "https://t.me/example", mes: "Привет" }
  }));
};
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

**Go** (gorilla/websocket)
```go
package main

import (
	"log"
	"net/url"
	"os"
	"os/signal"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
)

func main() {
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)

	u := url.URL{Scheme: "ws", Host: "msgw:8000", Path: "/chat/general"}
	c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
	if err != nil {
		log.Fatal("dial:", err)
	}
	defer c.Close()

	msg := map[string]interface{}{
		"uuid": uuid.New().String(),
		"ttl":  3600,
		"payload": map[string]interface{}{
			"typ": "send",
			"top": "https://t.me/example",
			"mes": "Привет из Go",
		},
	}
	c.WriteJSON(msg)

	go func() {
		for {
			var m map[string]interface{}
			if err := c.ReadJSON(&m); err != nil {
				log.Println("read:", err)
				return
			}
			log.Printf("received: %v\n", m)
		}
	}()

	<-interrupt
}
```

**Python** (websockets)
```python
import asyncio, json, uuid
import websockets

async def main():
    async with websockets.connect("ws://msgw:8000/chat/general") as ws:
        msg = {
            "uuid": str(uuid.uuid4()),
            "ttl": 3600,
            "payload": {
                "typ": "send",
                "top": "https://t.me/example",
                "mes": "Привет из Python"
            }
        }
        await ws.send(json.dumps(msg))
        async for message in ws:
            print(json.loads(message))

asyncio.run(main())
```

### Типы пакетов

**`send`** – обычное сообщение
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "ttl": 3600,
  "payload": {"typ": "send", "top": "https://t.me/example", "mes": "Важное"}
}
```
Сохраняется в Redis, рассылается всем подключённым. TTL обновляется при любой записи.

**`done`** – подтверждение
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "ttl": 3600,
  "payload": {"typ": "done"}
}
```
Перезаписывает старый пакет с тем же `uuid`. Новые клиенты получат `done`.

**`fail`** – ошибка
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "ttl": 3600,
  "payload": {"typ": "fail", "err": "Не удалось обработать"}
}
```
Аналогично `done` перезаписывает и уведомляет.

> Пакет можно перезаписывать повторно — продлевает TTL и вызывает повторную рассылку.

---

## Отправка через HTTP QUERY

Метод `QUERY`, не POST/GET.

```bash
curl -X QUERY http://msgw:8000/chat/general \
  -H "Content-Type: application/json" \
  -d '{
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
    "ttl": 3600,
    "payload": {"typ": "send", "top": "https://t.me/alerts", "mes": "Сервер перезагружен"}
  }'
```

Ответ:
```json
{"uuid":"550e8400-...","ttl":3600,"payload":{...}}
```
Сообщение сохранено и разослано (фоново). Статус `200 OK`.

---

## Reverse proxy с расшифровкой (ECIES)

Доступен только при `MSGW_ECIES_KEY`.

### Как это работает

1. POST на любой путь, например `/api/notify`.
2. Параметр `upstream` — полный URL бэкенда: `http://backend:8080/real/path`.
3. В теле ищутся `{{токен}}` (≥43 символа base64url без padding), расшифровываются, подставляются.
4. Запрос перенаправляется на `upstream`, заголовки копируются (кроме `Host` и `Content-Length`, длина пересчитывается).

Путь в запросе к msgw **не передаётся**.

### Query-параметры

- Все, кроме `upstream`, передаются на бэкенд.
- `upstream` удаляется.
- При дублировании приоритет у исходных параметров. Не дублируйте.

### Пример

```bash
curl -X POST "http://msgw:8000/api/notify?upstream=http://10.0.1.5:8080/process&chat_id=777" \
  -H "Content-Type: application/json" \
  -d '{"url":"tgram://bot123:{{it4Zp41QfKOgaCHQ4MY0muh...}}/chat456"}'
```

На бэкенд уйдёт `POST http://10.0.1.5:8080/process?chat_id=777` с расшифрованным токеном.

### Ошибки прокси

- **502 Bad Gateway**  
  Тело: `Upstream unreachable: [Errno 111] Connection refused`
- **500 Internal Server Error**  
  Тело: сообщение исключения.
- **Неудачная расшифровка** — токен остаётся как есть, запрос уходит на бэкенд. Не ошибка прокси.

---

## Генерация ключей

Приватный ключ: 32 байта → URL-safe Base64 без padding (ровно 43 символа).

**Генерация:**
```bash
# Через wg
wg genkey | basenc --base64url -w0 | tr -d '='

# Через openssl
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

**Публичный ключ** (для клиентов):
```bash
echo "приватный" | wg pubkey | basenc --base64url -w0 | tr -d '='
```

---

## Сценарии использования

### 1. Чат / уведомления
```
+--------+      +----------------+      +-------+
| Клиент |<---->| MessageCenter  |<---->| Redis |
| (WS)   |      +----------------+      +-------+
+--------+               ^
                   (QUERY)
                     |
                 +--------+
                 | Сервер |
                 +--------+
```
Сервер шлёт через QUERY, клиенты получают мгновенно. При отключении сообщения восстанавливаются.

### 2. Микросервисная шина с подтверждениями
```
+---------+          +----------------+          +---------+
| Сервис A|--QUERY-->| MessageCenter  |<--WS-->  | Сервис B|
+---------+          +----------------+          +---------+
                           |
                         Redis
```
A отправляет `send`, B обрабатывает и отвечает `done`. Если B упал, задача ждёт в Redis.

### 3. Прокси с расшифровкой
```
+--------+       +----------------+       +-----------+
| Клиент |--POST->| MessageCenter  |--POST->| Backend  |
+--------+       +----------------+       +-----------+
                      |
                 Приватный ключ
```
Клиент шифрует данные публичным ключом, оборачивает в `{{...}}`. msgw расшифровывает, бэкенд получает чистый JSON.

Сценарии 1 и 2 можно комбинировать. При недоступности WebSocket клиент может переключиться на HTTP QUERY.

---

## Переменные окружения

| Переменная                | По умолчанию | Назначение |
|---------------------------|--------------|------------|
| `MSGW_CACHE`              | `mem://`     | Хранилище (`redis://host:port/db`, `mongo://...`). Production – Redis. |
| `MSGW_CACHE_TTL`          | `3600`       | TTL по умолчанию (сек). |
| `MSGW_CACHE_BATCH_SIZE`   | `100`        | Ключей на итерацию SCAN (память при старте). |
| `MSGW_ECIES_KEY`          | (пусто)      | Приватный ключ (43 символа base64url). Не задан → прокси отключён. |
| `UVICORN_PORT`            | `8000`       | Порт. |
| `UVICORN_HOST`            | `0.0.0.0`    | Адрес. |
| `UVICORN_WORKERS`         | `1`          | Воркеры. |
| `UVICORN_LOG_LEVEL`       | `info`       | Уровень логирования. |
| `UVICORN_WS_PING_INTERVAL`| `3`          | Пинг WebSocket (сек). |
| `UVICORN_WS_PING_TIMEOUT` | `2`          | Таймаут понга (сек). |

Поддерживаются все переменные Uvicorn.

---

## Устранение неполадок

- **Нет старых сообщений при подключении** – проверьте Redis и `MSGW_CACHE_BATCH_SIZE`.
- **Не работает расшифровка** – проверьте `MSGW_ECIES_KEY` (43 символа, URL-safe Base64) и формат токенов.
- **Контейнер падает** – `docker logs msgw`, обычно проблема с Redis или `MSGW_CACHE`.
