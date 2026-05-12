# MessageCenter

Сервис на FastAPI: приём/доставка сообщений (WebSocket, HTTP-QUERY) + хранение в Redis, а также reverse proxy с расшифровкой ECIES.  
Контейнер, только ENV-конфигурация.

---

## Архитектура

```
+-------------+      +----------------+      +-------------------+
|  Клиенты    |      | MessageCenter  |      |  Redis / Backend  |
| WS / HTTP   |<---->|    :8000       |<---->|                   |
+-------------+      +----------------+      +-------------------+
        |                       |
        | Сообщения (WS, QUERY) | Прокси (POST + upstream)
        +-----------------------+
```

Два режима: **сообщения** (хранение + рассылка) и **прокси с расшифровкой** (только при заданном ключе).

---

## Быстрый старт

```bash
docker network create msgw-net
docker run -d --name redis --network msgw-net redis --appendonly yes

# Без расшифровки
docker run -d --name msgw --network msgw-net -p 8000:8000 \
  -e MSGW_CACHE=redis://redis:6379/0 \
  ghcr.io/grevinden/msgw:latest

# С расшифровкой (прокси активирован)
docker run -d --name msgw --network msgw-net -p 8000:8000 \
  -e MSGW_CACHE=redis://redis:6379/0 \
  -e MSGW_ECIES_KEY="ваш_приватный_ключ_base64url" \
  ghcr.io/grevinden/msgw:latest
```

Открыты:
- `ws://<хост>:8000/<путь>` – WebSocket
- `http://<хост>:8000/<путь>` – метод QUERY
- `POST http://<хост>:8000/<путь>?upstream=...` – прокси (при ключе)

---

## Отправка сообщений через WebSocket

### Примеры подключения

**JavaScript**
```javascript
const ws = new WebSocket("ws://msgw:8000/chat/general");
ws.onopen = () => ws.send(JSON.stringify({
  uuid: crypto.randomUUID(),
  ttl: 3600,
  payload: { typ: "send", top: "https://t.me/example", mes: "Привет" }
}));
ws.onmessage = e => console.log(JSON.parse(e.data));
```

**Go (gorilla/websocket)**
```go
u := url.URL{Scheme: "ws", Host: "msgw:8000", Path: "/chat/general"}
c, _, _ := websocket.DefaultDialer.Dial(u.String(), nil)
defer c.Close()
msg := map[string]interface{}{
    "uuid": uuid.New().String(), "ttl": 3600,
    "payload": map[string]interface{}{"typ":"send","top":"https://t.me/example","mes":"Привет"},
}
c.WriteJSON(msg)
// чтение входящих: c.ReadJSON(&m)
```

**Python (websockets)**
```python
async with websockets.connect("ws://msgw:8000/chat/general") as ws:
    await ws.send(json.dumps({
        "uuid": str(uuid.uuid4()), "ttl": 3600,
        "payload": {"typ":"send","top":"https://t.me/example","mes":"Привет"}
    }))
    async for msg in ws:
        print(json.loads(msg))
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
Сохраняется в Redis, рассылается всем. TTL обновляется при каждой записи.

**`done`** – подтверждение обработки
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "ttl": 3600,
  "payload": {"typ": "done"}
}
```
Перезаписывает старый пакет с тем же uuid. Новые клиенты получат `done`.

**`fail`** – ошибка обработки
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "ttl": 3600,
  "payload": {"typ": "fail", "err": "Не удалось обработать"}
}
```
Аналогично перезаписывает и уведомляет.

Пакет можно перезаписывать повторно – продлевает TTL и вызывает повторную рассылку.

---

## Отправка через HTTP QUERY

Метод `QUERY` (не POST/GET).

```bash
curl -X QUERY http://msgw:8000/chat/general \
  -H "Content-Type: application/json" \
  -d '{
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
    "ttl": 3600,
    "payload": {"typ": "send", "top": "https://t.me/alerts", "mes": "Сервер перезагружен"}
  }'
```

Ответ `200 OK`, идентичный отправленному JSON. Сообщение сохранено и разослано.

---

## Reverse proxy с расшифровкой (ECIES)

**Работает только при заданной переменной `MSGW_ECIES_KEY`.**  
Без ключа маршрут не регистрируется.

### Как это работает

1. POST на любой путь, например `/api/notify`.
2. Параметр `upstream` – полный URL бэкенда: `http://backend:8080/real/path`.
3. В теле ищутся `{{токен}}` (≥43 символа base64url без padding), расшифровываются, подставляются.
4. Запрос перенаправляется на `upstream`, заголовки копируются (кроме `Host` и `Content-Length`).

Путь в запросе к msgw **не передаётся** бэкенду.

### Query-параметры

- Все, кроме `upstream`, передаются на бэкенд.
- `upstream` удаляется.
- При дублировании приоритет у исходных параметров. Избегайте дублирования.

### Пример

```bash
curl -X POST "http://msgw:8000/api/notify?upstream=http://10.0.1.5:8080/process&chat_id=777" \
  -H "Content-Type: application/json" \
  -d '{"url":"tgram://bot123:{{it4Zp41QfKOgaCHQ4MY0muh...}}/chat456"}'
```

На бэкенд уйдёт `POST http://10.0.1.5:8080/process?chat_id=777` с расшифрованным токеном.

### Ошибки прокси

- **502 Bad Gateway** – нет соединения с upstream. Тело: `Upstream unreachable: [Errno 111] Connection refused`
- **500 Internal Server Error** – внутренняя ошибка. Тело: сообщение исключения.
- **Неудачная расшифровка** – токен остаётся без изменений, запрос уходит на бэкенд «как есть». Не ошибка прокси.

---

## Генерация ключей

Приватный ключ: 32 байта → URL-safe Base64 без padding (ровно 43 символа).

```bash
# Через wg (WireGuard)
wg genkey | basenc --base64url -w0 | tr -d '='

# Через openssl
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Публичный ключ для клиентов:
```bash
echo "приватный" | wg pubkey | basenc --base64url -w0 | tr -d '='
```

---

## Сценарии использования

### 1. Чат / уведомления в реальном времени
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

### 3. Прокси с прозрачной расшифровкой
```
+--------+       +----------------+       +-----------+
| Клиент |--POST->| MessageCenter  |--POST->| Backend  |
+--------+       +----------------+       +-----------+
                      |
                 Приватный ключ
```
Клиент шифрует данные публичным ключом, оборачивает в `{{...}}`. msgw расшифровывает, бэкенд получает чистый JSON.

Сценарии 1 и 2 можно объединять. При недоступности WebSocket клиент может переключиться на HTTP QUERY.

---

## Переменные окружения

| Переменная                | По умолчанию | Назначение |
|---------------------------|--------------|------------|
| `MSGW_CACHE`              | `mem://`     | Хранилище (`redis://host:port/db`, `mongo://...`). Production – Redis. |
| `MSGW_CACHE_TTL`          | `3600`       | TTL по умолчанию (сек). |
| `MSGW_CACHE_BATCH_SIZE`   | `100`        | Ключей на итерацию SCAN (память при старте). |
| `MSGW_ECIES_KEY`          | (пусто)      | Приватный ключ (43 символа base64url). Без него прокси отключён. |
| `UVICORN_PORT`            | `8000`       | Порт. |
| `UVICORN_HOST`            | `0.0.0.0`    | Адрес. |
| `UVICORN_WORKERS`         | `1`          | Воркеры. |
| `UVICORN_LOG_LEVEL`       | `info`       | Уровень логирования. |
| `UVICORN_WS_PING_INTERVAL`| `3`          | Пинг WebSocket (сек). |
| `UVICORN_WS_PING_TIMEOUT` | `2`          | Таймаут понга (сек). |

Поддерживаются все переменные Uvicorn.

---

Сервис не требует файлов конфигурации, управляется только ENV, подходит для Docker Compose, K8s, Nomad.
