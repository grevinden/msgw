# Тест прокси-эндпоинта

Интеграционный тест для проверки работы режима прокси MSGW. Тест отправляет **реальный проксированный запрос** на jsonplaceholder.typicode.com и проверяет ответ.

## Зачем это нужно

Прокси-эндпоинт MSGW работает так:

```
Клиент → POST /{path}?upstream=https://backend/api → health-чек → decrypt_bytes() → proxy_pass() → upstream
```

Этот тест проверяет **всю цепочку целиком**: шифрование → health-чек → расшифровка → проксирование → ответ от реального upstream.

## Как запустить

```bash
# Все тесты с подробным выводом
.venv/bin/python -m pytest tests/test_proxy.py -v -s

# Только тест с реальным upstream
.venv/bin/python -m pytest tests/test_proxy.py::TestProxyPost::test_proxy_post_real_upstream -v -s
```

> **Примечание:** Тест требует доступа в интернет (jsonplaceholder.typicode.com).

## Что проверяет

### Тест 1: `test_proxy_post_real_upstream`

Реальный POST-запрос через прокси на jsonplaceholder.typicode.com **без моков**:

| Шаг | Что происходит |
|-----|---------------|
| 1 | Тело запроса `{"test": "hello", "number": 42}` шифруется ECIES (X25519 + ChaCha20-Poly1305) |
| 2 | Отправляется POST `/post?upstream=https://jsonplaceholder.typicode.com/posts` с зашифрованным телом |
| 3 | LenientHealthChecker выполняет реальный TCP-чек порта upstream |
| 4 | MSGW расшифровывает тело, проксирует на jsonplaceholder |
| 5 | Проверяется ответ `201 Created` с полем `id` и заголовками `X-System-ID: upstream`, `X-Upstream-Status: healthy` |

### Тест 2: `test_proxy_dead_upstream`

Проверяет, что при нерабочем upstream возвращается `502`:

| Что | Результат |
|-----|-----------|
| Upstream | `https://dead.invalid` (DNS не резолвится) |
| Health checker | Реальный TCP-чек → порт недоступен → `is_healthy()` → `False` |
| Ответ | `502 Backend not healthy` |

## Ключевые моменты

- **Реальный upstream** — запрос уходит на jsonplaceholder.typicode.com, ответ приходит оттуда
- **Реальное шифрование** — тело шифруется ECIES, MSGW расшифровывает
- **Ничего не мокается** — health checker, проксирование, расшифровка — всё реально
- **Проверяется ответ** — парсится JSON от upstream, сравниваются данные

## Адаптация под свои нужды

Чтобы протестировать свой upstream:

1. Измените `upstream_url` на нужный адрес
2. Измените `plain_body` на ожидаемое тело запроса
3. Настройте assert-проверки под формат ответа вашего бэкенда

```python
# Пример: проверить свой бэкенд
upstream_url = "https://my-backend.example.com/api/data"
plain_body = json.dumps({"action": "create", "item": "test"})

# Проверка ответа
assert response_data["status"] == "ok"
assert response_data["item"] == "test"
```