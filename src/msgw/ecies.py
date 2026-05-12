import re
from base64 import urlsafe_b64decode , urlsafe_b64encode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey , X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretBytes

from .settings import Settings


def decrypt_x25519_chacha ( encrypted_b64: str , private_key_b64: str ) -> str :
	"""Аналог Rust-метода decrypt из methods.rs"""
	encrypted = urlsafe_b64decode ( encrypted_b64 + "=" )
	if len ( encrypted ) < 32 + 12 + 16 :
		raise ValueError ( "Зашифрованные данные слишком короткие" )

	ephemeral_public_bytes = encrypted [ :32 ]
	nonce = encrypted [ 32 :44 ]
	ciphertext = encrypted [ 44 : ]

	# Приватный ключ (32 байта)
	private_bytes = urlsafe_b64decode ( private_key_b64 + "=" )
	private_key = X25519PrivateKey.from_private_bytes ( private_bytes )

	# Ephemeral public key
	ephemeral_public = X25519PublicKey.from_public_bytes ( ephemeral_public_bytes )

	# Shared secret
	shared = private_key.exchange ( ephemeral_public )

	# HKDF: без соли, info = b"ecies-chacha20-poly1305"
	hkdf = HKDF (
		algorithm = hashes.SHA256 ( ) ,
		length = 32 ,
		salt = None ,
		info = b"ecies-chacha20-poly1305" ,
	)
	symmetric_key = hkdf.derive ( shared )

	# ChaCha20Poly1305 расшифровка
	chacha = ChaCha20Poly1305 ( symmetric_key )
	plaintext = chacha.decrypt ( nonce , ciphertext , None )
	return plaintext.decode ( "utf-8" )


def decrypt_bytes (
		*b: bytes , k = Settings.ecies_bytes.get_secret_value ( ) ,
		p: re.Pattern = re.compile ( r"\{\{([A-Za-z0-9_-]{43,})\}\}" )
) -> list [ SecretBytes ] :  #

	# Приватный ключ у нас в байтах (32 байта) – переводим в Base64 без паддинга
	private_b64 = urlsafe_b64encode ( k ).rstrip ( b"=" ).decode ( )

	def _replace ( match ) :
		token = match.group ( 1 )
		try :
			plain = decrypt_x25519_chacha ( token , private_b64 )
			return plain
		except Exception as e :
			return match.group ( 0 )  # оставляем {{токен}} без изменений

	return [ SecretBytes ( p.sub ( _replace , body.decode ( ) ).encode ( ) ) for body in b ]
