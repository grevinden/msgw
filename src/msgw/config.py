from typing import Final

from .environ import Settings

# noinspection PyArgumentList
settings: Final [ Settings ] = Settings ( )
header_system_id: Final[dict[str, str]] = {'X-System-ID': __package__}
