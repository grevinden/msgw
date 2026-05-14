Ниже представлен структурированный список всех переменных окружения для проекта `msgw`, основанный на файле `environ.py`.

**Префикс переменных:**
По умолчанию используется префикс `MSGW_` (определяется переменной `APP`, если она не задана, или значением `NAME`).
*Пример:* Если `APP` не задан, переменная кэша будет `MSGW_CACHE_URL`.

### 1. Кэш и Хранилище (`cache`)
Отвечает за подключение к Redis, MongoDB или использованию памяти.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_CACHE_URL` | `str` (URL) | `mem://` | URL подключения к бэкенду. Поддерживаемые схемы: `mem://`, `redis://`, `mongo://`. <br> *Для Redis:* `redis://host:port/db`<br> *Для Mongo:* `mongo://host:port/db` |
| `MSGW_CACHE_BATCH_SIZE` | `int` (>0) | `1` | Размер пакета для операции `SCAN` при восстановлении сообщений для нового WebSocket-клиента. Влияет на нагрузку при подключении. |
| `MSGW_CACHE_TTL` | `int` (>0) | `3600` | Время жизни сообщения в секундах (по умолчанию). Обновляется при каждой записи. |

### 2. Шифрование ECIES (`ecies`)
Используется для активации режима Reverse Proxy с расшифровкой токенов.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_ECIES_KEY` | `str` | `null` | Приватный ключ для расшифровки. **Обязательный формат:** 43 символа Base64Url (без знаков `=`). <br> *Если не задан, маршрут прокси `/` не регистрируется.* |

### 3. Прокси и Health Checks (`proxy`, `health`)
Настройки проверки доступности upstream-серверов.

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_PROXY_HOSTS` | `list[str]` | `null` | Список предустановленных URL для мониторинга здоровья (Health Check). <br> *Пример:* `["http://backend1:8080", "http://backend2:8080"]` |
| `MSGW_HEALTH_TIMEOUT` | `int` | `2` | Таймаут (в секундах) ожидания ответа от upstream при проверке здоровья. |
| `MSGW_HEALTH_INTERVAL` | `int` | `3` | Интервал (в секундах) между проверками здоровья upstream-серверов. |

### 4. Интеграция с LLM (`llm`)
Настройки для эндпоинта отладки `/api/llm` (через OpenRouter или совместимые API).

| Переменная | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `MSGW_LLM_API_URL` | `str` (URL) | `null` | Базовый URL API провайдера. <br> *Пример:* `https://openrouter.ai/api/v1` |
| `MSGW_LLM_API_KEY` | `str` | `null` | API ключ провайдера. <br> *Пример:* `sk-or-v1-...` |
| `MSGW_LLM_MODEL` | `str` | `null` | Идентификатор модели. <br> *Пример:* `openrouter/openai/gpt-4o-mini` |

---

### Пример `.env` файла для продакшена (Redis + Proxy + LLM)

```env
# --- Cache ---
MSGW_CACHE_URL=redis://redis:6379/0
MSGW_CACHE_TTL=7200
MSGW_CACHE_BATCH_SIZE=100

# --- ECIES Proxy ---
# Сгенерирован через: wg genkey | basenc --base64url -w0 | tr -d '='
MSGW_ECIES_KEY=SGVsbG9Xb3JsZFRoaXNJc0FUb25nS2V5Rm9yVGVzdGluZw

# --- Health Checks ---
MSGW_HEALTH_TIMEOUT=5
MSGW_HEALTH_INTERVAL=10
MSGW_PROXY_HOSTS=["http://backend-api:8000"]

# --- LLM Debugging ---
MSGW_LLM_API_URL=https://openrouter.ai/api/v1
MSGW_LLM_API_KEY=sk-or-v1-YOUR_KEY_HERE
MSGW_LLM_MODEL=openrouter/anthropic/claude-3-haiku
```

### Важные примечания:
1.  **Вложенность:** В коде используется `env_nested_delimiter=''` (пустая строка), но имена классов (`Cache`, `Ecies`) и полей объединяются через подчеркивание в соответствии со стандартным поведением `pydantic-settings`, если не указано иное. Однако, судя по коду `Settings`, поля объявлены как аннотированные атрибуты внутри класса, но без явного указания `model_config = SettingsConfigDict(env_nested_delimiter='__')`. 
    *   *Уточнение:* В предоставленном коде `environ.py` используется структура с вложенными классами (`class Cache`, `class Ecies`), но они присвоены переменным уровня класса (`cache: Annotated[...]`, `ecies: Annotated[...]`). 
    *   Pydantic V2 по умолчанию ожидает разделитель `__` для вложенных моделей. Если в коде не переопределен `env_nested_delimiter`, переменные должны называться так: `MSGW_CACHE__URL`, `MSGW_ECIES__KEY`. 
    *   **Однако**, в коде есть `env_nested_delimiter = ''`. Это может привести к конфликтам имен или плоской структуре. Рекомендуется проверить экспериментально. Если переменные не подхватываются, попробуйте плоские имена (как в таблице выше) или с двойным подчеркиванием (`MSGW_CACHE__URL`).

2.  **Безопасность:** Никогда не коммитьте `MSGW_ECIES_KEY` и `MSGW_LLM_API_KEY` в репозиторий. Используйте секреты Docker или Kubernetes.
