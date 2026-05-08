from os import environ
from typing import Final , Literal , Annotated , Any , override

import cashews_mongo  # noqa
import yarl
from cashews import cache
from pydantic import UrlConstraints , Field , AnyUrl , PositiveInt
from pydantic_settings import BaseSettings

NAME: Final [ Literal [ "MSGW" ] ] = 'MSGW'


class Settings (
	BaseSettings ,
	env_prefix = environ.get ( 'APP' , NAME ).strip ( '_' ) + '_' ,
	case_sensitive = False ,
) :
	class CashewsUrl ( AnyUrl ) :
		_constraints = UrlConstraints ( allowed_schemes = [ 'mem' , 'mongo' , 'redis' ] )

	cache: Annotated [ CashewsUrl , Field ( CashewsUrl ( "mem://" ) ) ]
	cache_batch_size: Annotated [ PositiveInt , Field ( 1 ) ]
	cache_ttl: Annotated [ PositiveInt , Field ( 3600 ) ]

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


# noinspection PyArgumentList
Settings: Final [ Settings ] = Settings ( )
