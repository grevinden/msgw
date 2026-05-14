# llm.py
import litellm

from .config import settings

# Отключаем телеметрию для приватности
litellm.telemetry = False

async def ask_llm(error_text: str, prompt: str) -> str:
    """
    Отправляет запрос в LLM через LiteLLM, используя настройки из ENV.
    """
    if not settings.llm.api.key:
        raise ValueError("LLM API key is not configured (MSGW_LLM_API_KEY)")

    system_prompt = (
        "Ты — старший инженер-разработчик Python, эксперт в архитектуре MSGW. "
        "Твоя задача — анализировать ошибки и давать четкие технические решения на русском языке. "
        "Будь лаконичен, используй профессиональный тон. "
        "Ссылайся на конкретные модули (model.py, ecies.py и т.д.), если это уместно."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Error Log/Context:\n{error_text}\n\nUser Question:\n{prompt}"}
    ]

    try:
        # Подготовка параметров
        kwargs = {
            "model": settings.llm.model,
            "messages": messages,
            "api_key": settings.llm.api.key.get_secret_value(),
        }

        # Если задан базовый URL, добавляем его
        if settings.llm.api.url:
            kwargs["api_base"] = settings.llm.api.url.unicode_string()

        # Выполнение запроса
        response = await litellm.acompletion(**kwargs)

        # Извлечение ответа
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            return content if content else "Empty response from LLM"
        else:
            return "No choices returned from LLM"

    except Exception as e:
        # Возвращаем понятную ошибку, чтобы не ломать весь endpoint
        return f"LLM Service Error: {type(e).__name__}: {str(e)}"
