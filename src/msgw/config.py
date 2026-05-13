from typing import Final

from .settings import Settings, NAME

# noinspection PyArgumentList
settings: Final [ Settings ] = Settings ( )
header_system_id: Final[dict[str, str]] = {'X-System-ID': __package__}
