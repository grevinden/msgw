В файле `environ.py` представлена конфигурация приложения через вложенные модели Pydantic. Из-за использования `env_nested_delimiter = ''` (пустая строка) и структуры классов, переменные окружения формируются путем конкатенации префикса, имени класса и имени поля.

**Глобальный префикс:** `MSGW_` (если не задана переменная `APP`).

Ниже приведен полный список переменных окружения, сгруппированный по логическим блокам.

### 1. Кэш (`cache`)
Отвечает за подключение к хранилищу состояний (Redis, MongoDB или память).

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_CACHE_URL` | `str` (URL) | `mem://` | URL подключения. Поддерживаемые схемы: `mem://`, `redis://`, `mongo://`. <br> *Пример:* `redis://localhost:6379/0` |
| `MSGW_CACHE_BATCH_SIZE` | `int` | `1` | Размер пакета для сканирования ключей при восстановлении истории для нового WebSocket-клиента. |
| `MSGW_CACHE_TTL` | `int` | `3600` | Время жизни сообщения в секундах (по умолчанию). |

> **Важно:** В методе `model_post_init` для схемы `redis` автоматически добавляются параметры `pickle_type=null` и `client_side=True`.

### 2. Шифрование ECIES (`ecies`)
Активирует режим Reverse Proxy с расшифровкой токенов.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_ECIES_KEY` | `str` | `null` | Приватный ключ для расшифровки. **Строгий формат:** ровно 43 символа Base64Url (без знаков `=`). <br> *Если не задан, прокси-маршруты не регистрируются.* |

*Доступны также вычисляемые поля (только для чтения внутри приложения):*
*   `MSGW_ECIES_BYTES` — декодированные байты ключа.
*   `MSGW_ECIES_ENABLED` — `true`, если ключ задан.

### 3. Прокси (`proxy`)
Настройки upstream-серверов.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_PROXY_HOSTS` | `list[str]` | `null` | Список URL для предварительной регистрации в Health Checker. <br> *Пример:* `["http://backend1:8080", "http://backend2:8080"]` |

*Вычисляемое поле:*
*   `MSGW_PROXY_ENABLED` — `true`, если список хостов не пуст.

### 4. Health Checks (`health`)
Параметры проверки доступности upstream-серверов.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_HEALTH_TIMEOUT` | `int` | `2` | Таймаут ожидания ответа от сервера при проверке здоровья (в секундах). |
| `MSGW_HEALTH_INTERVAL` | `int` | `3` | Интервал между проверками здоровья (в секундах). |

*Вычисляемое поле:*
*   `MSGW_HEALTH_ENABLED` — `true`, если таймаут и интервал заданы.

### 5. LLM / Отладка (`llm`)
Настройки для интеграции с языковыми моделями (через OpenRouter или совместимые API).

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_LLM_API_URL` | `str` (URL) | `null` | Базовый URL API провайдера. <br> *Пример:* `https://openrouter.ai/api/v1` |
| `MSGW_LLM_API_KEY` | `str` | `null` | API ключ провайдера. |
| `MSGW_LLM_MODEL` | `str` | `null` | Идентификатор модели. <br> *Пример:* `openrouter/openai/gpt-4o-mini` |

*Вычисляемое поле:*
*   `MSGW_LLM_ENABLED` — `true`, если заданы и URL, и ключ.

### 6. Системные пути (`path`)
Служебная информация о расположении файлов.

| Переменная | Тип | Описание |
| :--- | :--- | :--- |
| `MSGW_PATH_ROOT` | `str` (Path) | Абсолютный путь к корневой директории проекта (вычисляется автоматически). |

---

### Пример `.env` файла для запуска с Redis и Proxy

```env
# Cache Configuration
MSGW_CACHE_URL=redis://redis:6379/0
MSGW_CACHE_TTL=7200
MSGW_CACHE_BATCH_SIZE=50

# ECIES Proxy Configuration
# Сгенерирован через: wg genkey | basenc --base64url -w0 | tr -d '='
MSGW_ECIES_KEY=SGVsbG9Xb3JsZFRoaXNJc0FUb25nS2V5Rm9yVGVzdGluZw

# Health Checks
MSGW_HEALTH_TIMEOUT=5
MSGW_HEALTH_INTERVAL=10
MSGW_PROXY_HOSTS=["http://backend-api:8000"]

# LLM Debugging
MSGW_LLM_API_URL=https://openrouter.ai/api/v1
MSGW_LLM_API_KEY=sk-or-v1-YOUR_SECRET_KEY
MSGW_LLM_MODEL=openrouter/anthropic/claude-3-haiku
```

### Примечание по именованию
Так как в классе `Settings` указан `env_nested_delimiter = ''`, Pydantic будет ожидать переменные в формате `PREFIX_CLASS_FIELD`.
Однако, стандартное поведение `pydantic-settings` при вложенных моделях часто использует двойное подчеркивание `__`. Если переменные выше не подхватываются, попробуйте вариант с разделителем:
*   `MSGW_CACHE__URL`
*   `MSGW_ECIES__KEY`
*   `MSGW_LLM__API__URL`

*(В предоставленном коде `environ.py` delimiter пустой, но рекомендуется проверить экспериментально, так как это зависит от версии `pydantic-settings`. Если используется плоская структура `settings.py`, то имена будут `MSGW_CACHE_URL` и т.д.)*
