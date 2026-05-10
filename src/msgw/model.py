from typing import Literal , Annotated
from uuid import UUID

from pydantic import BaseModel , UUID4 , Field , computed_field , PositiveInt , SecretStr , AnyUrl , field_serializer
from pydantic_core import from_json
from ulid import ULID

# noinspection PyDataclass
from .settings import Settings


# noinspection PyDataclass
class MessageSend ( BaseModel , frozen = True ) :
	typ: Literal [ "send" ]
	top: AnyUrl
	mes: Annotated [ SecretStr , Field ( min_length = 1 , strict = True ) ]

	@field_serializer ( 'mes' , mode = 'plain' )
	def serialize_top ( self , v: SecretStr ) -> str :
		return v.get_secret_value ( )


# noinspection PyDataclass
class MessageDone ( BaseModel , frozen = True ) :
	typ: Literal [ "done" ]


# noinspection PyDataclass
class MessageFail ( BaseModel , frozen = True ) :
	typ: Literal [ "fail" ]
	err: str | list [ str ]


# noinspection PyDataclass
class Message ( BaseModel , frozen = True ) :
	ttl: Annotated [ PositiveInt , Field ( Settings.cache_ttl ) ]
	uuid: Annotated [ UUID4 , Field ( title = "UUID4" ) ]
	payload: Annotated [
		MessageSend | MessageDone | MessageFail ,
		Field ( discriminator = "typ" , title = "PAYLOAD" , description = "Содержимое пакета данных" ) ,
	]

	@computed_field ( title = "ULID" )
	@property
	def ulid ( self ) -> ULID :
		return ULID ( )

	@computed_field ( title = "TYPE" )
	@property
	def typ ( self ) -> Literal [ "receipt" , "notify" , "unknown" ] :
		match self.payload.typ :
			case "send" :
				return "notify"
			case "done" | "fail" :
				return "receipt"
			case _ :
				return "unknown"

	@classmethod
	def from_json ( cls , text: str , / ) -> "Message | None" :
		try :
			return cls.model_validate_json ( text )
		except Exception as exc :
			try :
				data = from_json ( text , allow_partial = True )
				uuid = UUID ( data.get ( "uuid" ) )
				ttl = int ( data.get ( "ttl" , Settings.cache_ttl ) )
				return cls.model_validate (
					{
						"uuid"    : uuid , ttl : ttl if ttl > 0 else Settings.cache_ttl ,
						"payload" : { "typ" : "fail" , "err" : str ( exc ) } ,
					} ,
				)
			except Exception :
				return None
