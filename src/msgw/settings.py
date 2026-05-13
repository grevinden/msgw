from base64 import urlsafe_b64decode
from os import environ
from pathlib import Path
from re import fullmatch
from typing import Final , Literal , Annotated , Any , override

import cashews_mongo  # noqa
import yarl
from cashews import cache
from pydantic import UrlConstraints , Field , AnyUrl , PositiveInt , computed_field , SecretStr , SecretBytes , \
	field_validator , HttpUrl
from pydantic_settings import BaseSettings , SettingsError

NAME: Final [ Literal [ "MSGW" ] ] = 'MSGW'


class Settings (
	BaseSettings ,
	env_prefix = environ.get ( 'APP' , NAME ).strip ( '_' ) + '_' ,
	case_sensitive = False , validate_default = False ,
) :
	class CashewsUrl ( AnyUrl ) :
		_constraints = UrlConstraints ( allowed_schemes = [ 'mem' , 'mongo' , 'redis' ] )

	cache: Annotated [ CashewsUrl , Field ( CashewsUrl ( "mem://" ) ) ]
	cache_batch_size: Annotated [ PositiveInt , Field ( 1 ) ]
	cache_ttl: Annotated [
		PositiveInt ,
		Field ( 3600 , description = 'Время жизни кеша, по-умолчанию' )
	]
	ecies_key: Annotated [
		SecretStr | None ,
		Field ( None , title = 'Ключ шифрования' )
	]
	known_hosts: Annotated [
		list [ HttpUrl ] | None ,
		Field ( None , description = 'Предустановленный список http-адресов'
		                             ' для проверки на активность' )
	]
	health_checker_timeout: Annotated [
		int ,
		Field ( 2 , description = 'Таймаут проверки доступности'
		                          ' вышестоящего сервера при проксировании' )
	]
	health_checker_interval: Annotated [
		int ,
		Field ( 3 , description = 'Интервал проверки проверки доступности'
		                          ' вышестоящего сервера при проксировании' )
	]

	@override
	def model_post_init ( self , context: Any , / ) -> bool | None :
		try :
			return super ( ).model_post_init ( context )
		finally :
			url = yarl.URL ( self.cache.unicode_string ( ) )
			match self.cache.scheme :
				case 'redis' :
					url = url.update_query ( {
						'pickle_type' : 'null' ,
						'client_side' : 'True' ,
					} )

			cache.setup (
				settings_url = url.human_repr ( ) ,
				suppress = __debug__ ,
			)

	@computed_field
	def path_root ( self ) -> Path :
		return Path ( __file__ ).parent.parent.parent

	@computed_field
	def ecies_bytes ( self ) -> SecretBytes | None :
		if self.ecies_key :
			return SecretBytes ( urlsafe_b64decode ( self.ecies_key.get_secret_value ( ) + "=" ) )
		return None

	# noinspection PyNestedDecorators
	@field_validator ( 'ecies_key' )
	@classmethod
	def ecies_key_validator ( cls , v: SecretStr ) -> SecretStr :
		if v and not fullmatch ( r'[A-Za-z0-9_-]{43}' , v.get_secret_value ( ) ) :
			raise SettingsError ( r'Ключ не соответствует формату [A-Za-z0-9_-]{43}' )
		return v
