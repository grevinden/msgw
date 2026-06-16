"""
Интеграционный тест прокси-эндпоинта с реальным upstream.

Проверяет:
1. Шифрование тела запроса (ECIES)
2. LenientHealthChecker — первый запрос к новому хосту включает синхронный TCP-чек (до 2 сек)
3. Реальное проксирование POST-запроса на jsonplaceholder
4. Расшифровка тела и пересылка на upstream
"""

import base64
import json
import logging
import os
import sys

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from starlette.testclient import TestClient

logger = logging.getLogger("proxy_test")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(handler)

TEST_ECIES_KEY = "Fyv5fnaWOMJ__4wt18zEqIUuoYXAxIT2w3Gc99PfKo8"


def encrypt_body(plain_text: str, private_key_bytes: bytes) -> str:
    """Шифрует тело запроса ECIES (X25519 + ChaCha20-Poly1305)."""
    ephemeral_key = X25519PrivateKey.generate()

    server_public_key = X25519PrivateKey.from_private_bytes(
        private_key_bytes
    ).public_key()
    shared_secret = ephemeral_key.exchange(server_public_key)

    encryption_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"ecies-chacha20-poly1305",
    ).derive(shared_secret)

    nonce = os.urandom(12)

    cipher = ChaCha20Poly1305(encryption_key)
    ciphertext = cipher.encrypt(nonce, plain_text.encode(), None)

    encrypted = ephemeral_key.public_key().public_bytes_raw() + nonce + ciphertext

    token = base64.urlsafe_b64encode(encrypted).rstrip(b"=").decode()
    return f"{{{{{token}}}}}"


@pytest.fixture
def ecies_key_bytes() -> bytes:
    return base64.urlsafe_b64decode(TEST_ECIES_KEY + "=")


@pytest.fixture
def app():
    os.environ["MSGW_ECIES_KEY"] = TEST_ECIES_KEY
    os.environ["MSGW_CACHE_URL"] = "mem://?check_interval=1"
    os.environ["MSGW_CACHE_TTL"] = "3600"

    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("msgw"):
            del sys.modules[mod_name]

    from src.msgw.core import app

    yield app


class TestProxyPost:
    """Реальные тесты прокси — без моков."""

    def test_proxy_post_real_upstream(self, app, ecies_key_bytes):
        """Реальный POST через прокси на jsonplaceholder.typicode.com.

        Без моков — health checker, проксирование, расшифровка — всё реально.
        """
        plain_body = json.dumps({"test": "hello", "number": 42})
        upstream_url = "https://jsonplaceholder.typicode.com/posts"

        logger.info("=" * 70)
        logger.info("TEST: Real Proxy POST to jsonplaceholder (no mocks)")
        logger.info("=" * 70)

        encrypted_body = encrypt_body(plain_body, ecies_key_bytes)

        logger.info(f"  Upstream: {upstream_url}")
        logger.info(f"  Plain body: {plain_body}")

        with TestClient(app) as client:
            response = client.post(
                "/post",
                params={"upstream": upstream_url},
                content=encrypted_body,
                headers={"Content-Type": "application/json"},
            )

        logger.info(f"  Status: {response.status_code}")
        logger.info(f"  Headers: {dict(response.headers)}")
        logger.info(f"  Body (first 200): {response.text[:200]}")

        assert response.status_code in (200, 201, 405), (
            f"Expected 200/201/405, got {response.status_code}. "
            f"Body: {response.text[:200]}"
        )

        # jsonplaceholder возвращает 201 Created с body {"id": 101}
        assert response.status_code == 201
        response_data = response.json()
        assert "id" in response_data
        assert response.headers.get("X-System-ID") == "upstream"
        assert response.headers.get("X-Upstream-Status") == "healthy"

        logger.info("TEST PASSED!")

    def test_proxy_dead_upstream(self, app, ecies_key_bytes):
        """Dead upstream → 503 (health-чек заблокировал).

        Первый запрос к несуществующему хосту проходит TCP-чек
        (DNS не резолвится → порт закрыт → 503).
        """
        plain_body = json.dumps({"test": "dead"})
        encrypted_body = encrypt_body(plain_body, ecies_key_bytes)

        logger.info("=" * 70)
        logger.info("TEST: Dead upstream → 503")
        logger.info("=" * 70)

        with TestClient(app) as client:
            response = client.post(
                "/post",
                params={"upstream": "https://dead.invalid"},
                content=encrypted_body,
            )

        logger.info(f"  Status: {response.status_code}")
        logger.info(f"  Body: {response.text}")

        assert response.status_code == 503
        assert "Backend not healthy" in response.text

        logger.info("TEST PASSED!")
