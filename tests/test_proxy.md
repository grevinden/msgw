# Тест прокси-эндпоинта

Интеграционный тест для проверки работы режима прокси MSGW. Тест отправляет **реальный проксированный запрос** на httpbin.org и проверяет ответ.

## Зачем это нужно

Прокси-эндпоинт MSGW работает так:

```
Клиент → POST /{path}?upstream=https://backend/api → decrypt_bytes() → health-чек → proxy_pass() → upstream
```

Этот тест проверяет **всю цепочку целиком**: шифрование → расшифровка → health-чек → проксирование → ответ от реального upstream.

## Как запустить

```bash
# Все тесты с подробным выводом
.venv/bin/python -m pytest tests/test_proxy.py -v -s

# Только тест с реальным upstream
.venv/bin/python -m pytest tests/test_proxy.py::TestProxyPost::test_proxy_post_to_httpbin -v -s
```

> **Примечание:** Тест требует доступа в интернет (httpbin.org).

## Что проверяет

### Тест 1: `test_proxy_post_to_httpbin`

Реальный POST-запрос через прокси на httpbin.org:

| Шаг | Что происходит |
|-----|---------------|
| 1 | Тело запроса `{"test": "hello", "number": 42}` шифруется ECIES (X25519 + ChaCha20-Poly1305) |
| 2 | Health checker мокается (upstream считается healthy) |
| 3 | Отправляется POST `/post?upstream=https://httpbin.org/post` с зашифрованным телом |
| 4 | MSGW расшифровывает тело, проксирует на httpbin.org |
| 5 | Проверяется ответ от httpbin: `json` содержит `{"test": "hello", "number": 42}` |

### Тест 2: `test_proxy_post_unhealthy_upstream`

Проверяет, что при нерабочем upstream возвращается `503`:

| Что | Результат |
|-----|-----------|
| Health checker | `is_healthy()` → `False` |
| Ответ | `503 Backend not healthy` |
| Заголовок | `X-Upstream-Status: unhealthy` |

## Ключевые моменты

- **Реальный upstream** — запрос уходит на httpbin.org, ответ приходит оттуда
- **Реальное шифрование** — тело шифруется ECIES, MSGW расшифровывает
- **Мокается только health checker** — чтобы не зависеть от реального health-чека httpbin
- **Проверяется ответ** — парсится JSON от httpbin, сравниваются данные

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
