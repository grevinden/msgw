"""
Интеграционный тест прокси-эндпоинта с реальным upstream (httpbin.org).

Что проверяет:
1. Шифрование тела запроса (ECIES)
2. Health-чек upstream
3. Реальный проксированный POST-запрос на httpbin.org
4. Проверка ответа от httpbin.org

Логирование: каждый шаг выводит подробную информацию о том,
какой запрос куда отправляется и какой ответ получен.
"""

import base64
import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from starlette.testclient import TestClient

# Настраиваем логирование
logger = logging.getLogger("proxy_test")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(handler)

# Тестовый ключ ECIES (32 байта в base64url без padding)
TEST_ECIES_KEY = "Fyv5fnaWOMJ__4wt18zEqIUuoYXAxIT2w3Gc99PfKo8"


def encrypt_body(plain_text: str, private_key_bytes: bytes) -> str:
    """Шифрует тело запроса ECIES (X25519 + ChaCha20-Poly1305)."""
    ephemeral_key = X25519PrivateKey.generate()

    logger.info(
        f"  Ephemeral public key (32 bytes): "
        f"{base64.urlsafe_b64encode(ephemeral_key.public_key().public_bytes_raw()).decode()}"
    )

    # Вычисляем общий секрет (обмен с публичным ключом сервера)
    server_public_key = X25519PrivateKey.from_private_bytes(
        private_key_bytes
    ).public_key()
    shared_secret = ephemeral_key.exchange(server_public_key)

    # Производим ключ шифрования
    encryption_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"ecies-chacha20-poly1305",
    ).derive(shared_secret)

    logger.info(f"  Encryption key (SHA256): {encryption_key.hex()}")

    # Создаём nonce (12 байт)
    nonce = os.urandom(12)
    logger.info(f"  Nonce (12 bytes): {nonce.hex()}")

    # Шифруем
    cipher = ChaCha20Poly1305(encryption_key)
    ciphertext = cipher.encrypt(nonce, plain_text.encode(), None)

    # Формируем: ephemeral_public(32) + nonce(12) + ciphertext
    encrypted = ephemeral_key.public_key().public_bytes_raw() + nonce + ciphertext

    # Паттерн для замены: {{зашифрованные_данные}}
    token = base64.urlsafe_b64encode(encrypted).rstrip(b"=").decode()
    logger.info(f"  Encrypted token (base64url): {token[:60]}...")

    return f"{{{{{token}}}}}"


@pytest.fixture
def ecies_key_bytes() -> bytes:
    """Байты тестового ключа."""
    return base64.urlsafe_b64decode(TEST_ECIES_KEY + "=")


@pytest.fixture
def app():
    """Создаёт FastAPI-приложение с включённым прокси."""
    import sys

    os.environ["MSGW_ECIES_KEY"] = TEST_ECIES_KEY
    os.environ["MSGW_CACHE_URL"] = "mem://?check_interval=1"
    os.environ["MSGW_CACHE_TTL"] = "3600"

    # Очищаем кэш импортов для корректной инициализации
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("msgw"):
            del sys.modules[mod_name]

    from src.msgw.core import app

    yield app


class TestProxyPost:
    """Тесты прокси-эндпоинта с реальным upstream (httpbin.org)."""

    def test_proxy_post_to_httpbin(self, app, ecies_key_bytes):
        """
        Реальный POST-запрос через прокси на httpbin.org/post.

        Сценарий:
        1. Готовим тело запроса (JSON)
        2. Шифруем его ECIES
        3. Отправляем POST на /post?upstream=https://httpbin.org/post
        4. MSGW расшифровывает, проверяет health, проксирует на httpbin
        5. Проверяем ответ от httpbin — он должен содержать наши данные
        """
        # Исходные данные, которые мы хотим отправить upstream
        plain_body = json.dumps({"test": "hello", "number": 42})
        upstream_url = "https://httpbin.org/post"

        logger.info("=" * 70)
        logger.info("TEST: Real Proxy POST to httpbin.org")
        logger.info("=" * 70)

        # Шаг 1: Шифрование тела запроса
        logger.info("\nStep 1: Encrypting request body (ECIES)")
        logger.info(f"  Plain text to send: {plain_body}")

        encrypted_body = encrypt_body(plain_body, ecies_key_bytes)
        logger.info(f"  Encrypted body: {encrypted_body[:60]}...")

        # Шаг 2: Мокаем health checker (httpbin не нужен для health-чека)
        logger.info("\nStep 2: Mocking health checker")

        mock_checker = MagicMock()
        mock_checker.is_healthy.return_value = True
        mock_checker.interval = 3

        # Шаг 3: Отправляем реальный запрос через прокси
        logger.info("\nStep 3: Sending proxied request")
        logger.info(f"  URL: POST /post?upstream={upstream_url}")
        logger.info(f"  Body: {encrypted_body[:60]}...")
        logger.info("  Headers: Content-Type: application/json")

        with patch(
            "src.msgw.health_registry.checker",
            return_value=mock_checker,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/post",
                    params={"upstream": upstream_url},
                    content=encrypted_body,
                    headers={"Content-Type": "application/json"},
                )

        # Шаг 4: Анализ ответа от httpbin
        logger.info("\nStep 4: Analyzing httpbin response")
        logger.info(f"  Status code: {response.status_code}")
        logger.info(f"  Response headers: {dict(response.headers)}")

        # httpbin возвращает JSON с информацией о запросе
        response_data = response.json()
        logger.info(f"  Response JSON keys: {list(response_data.keys())}")

        # Шаг 5: Проверка данных
        logger.info("\nStep 5: Verifying data")

        # Проверяем, что httpbin получил наши данные
        logger.info(f"  httpbin.json: {response_data.get('json')}")
        logger.info(f"  httpbin.data: {response_data.get('data')}")
        logger.info(f"  httpbin.url: {response_data.get('url')}")

        # Проверки
        assert response.status_code == 200

        # httpbin возвращает распарсенный JSON в поле 'json'
        assert response_data["json"] == {"test": "hello", "number": 42}

        # Или в поле 'data' как строку
        assert response_data["data"] == plain_body

        # URL должен содержать /post
        assert "/post" in response_data["url"]

        # Заголовки прокси
        assert response.headers.get("X-System-ID") == "upstream"
        assert response.headers.get("X-Upstream-Status") == "healthy"

        logger.info("\nTEST PASSED!")
        logger.info("=" * 70)

    def test_proxy_post_unhealthy_upstream(self, app, ecies_key_bytes):
        """Тест: upstream не проходит health-чек → 503."""
        plain_body = json.dumps({"test": "unhealthy"})
        encrypted_body = encrypt_body(plain_body, ecies_key_bytes)

        logger.info("=" * 70)
        logger.info("TEST: Unhealthy upstream → 503")
        logger.info("=" * 70)

        logger.info("\nSending request to unhealthy upstream")
        logger.info("  URL: POST /post?upstream=https://httpbin.org")

        mock_checker = MagicMock()
        mock_checker.is_healthy.return_value = False
        mock_checker.interval = 3

        with patch(
            "src.msgw.health_registry.checker",
            return_value=mock_checker,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/post",
                    params={"upstream": "https://httpbin.org"},
                    content=encrypted_body,
                )

        logger.info("\nResult:")
        logger.info(f"  Status code: {response.status_code}")
        logger.info(f"  Response: {response.text}")

        assert response.status_code == 503
        assert "Backend not healthy" in response.text
        assert response.headers.get("X-Upstream-Status") == "unhealthy"

        logger.info("\nTEST PASSED!")
        logger.info("=" * 70)
