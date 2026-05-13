import re
from base64 import urlsafe_b64decode , urlsafe_b64encode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey , X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretBytes

from .config import settings


def decrypt_x25519_chacha ( encrypted_b64: str , private_key_b64: str ) -> str :
	if len ( encrypted := urlsafe_b64decode ( encrypted_b64 + "=" ) ) < 32 + 12 + 16 :
		raise ValueError ( "Зашифрованные данные слишком короткие" )

	return ChaCha20Poly1305 ( HKDF (
		algorithm = hashes.SHA256 ( ) , length = 32 ,
		salt = None , info = b"ecies-chacha20-poly1305" ,
	).derive ( X25519PrivateKey.from_private_bytes (
		urlsafe_b64decode ( private_key_b64 + "=" ) ).exchange (
		X25519PublicKey.from_public_bytes ( encrypted [ :32 ] ) ) ) ).decrypt (
		encrypted [ 32 :44 ] , encrypted [ 44 : ] , None ).decode ( "utf-8" )


if settings.ecies_key :
	def decrypt_bytes (
			*b: bytes , k = settings.ecies_bytes.get_secret_value ( ) ,
			p: re.Pattern = re.compile ( r"[{]{2}([A-Z0-9_-]{43,})[}]{2}" ,
			                             flags = re.IGNORECASE | re.UNICODE )
	) -> list [ SecretBytes ] :  #

		# Приватный ключ у нас в байтах (32 байта) – переводим в Base64 без паддинга
		private_b64 = urlsafe_b64encode ( k ).rstrip ( b"=" ).decode ( )

		def _replace ( match ) :
			token = match.group ( 1 )
			# noinspection PyBroadException
			try :
				plain = decrypt_x25519_chacha ( token , private_b64 )
				return plain
			except :
				return match.group ( 0 )  # оставляем {{токен}} без изменений

		return [ SecretBytes ( p.sub ( _replace , body.decode ( ) ).encode ( ) ) for body in b ]
