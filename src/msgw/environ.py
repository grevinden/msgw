from base64 import urlsafe_b64decode
from os import environ
from pathlib import Path
from re import fullmatch
from typing import Any, Final, Literal, override

import cashews_mongo  # noqa
import yarl
from cashews import cache
from pydantic import (
    AnyUrl,
    BaseModel,
    PositiveInt,
    SecretBytes,
    SecretStr,
    UrlConstraints,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsError

NAME: Final[Literal["MSGW"]] = "MSGW"


class Settings(
    BaseSettings,
    env_nested_delimiter="_",
    env_prefix=environ.get("APP", NAME).strip("_") + "_",
    case_sensitive=False,
    validate_default=False,
):  #
    class Cache(BaseModel):
        class CashewsUrl(AnyUrl):
            _constraints = UrlConstraints(allowed_schemes=["mem", "mongo", "redis"])

        url: CashewsUrl = CashewsUrl("mem://")
        ttl: PositiveInt = 3600

        @override
        def model_post_init(self, context: Any, /) -> None:
            super().model_post_init(context)
            url = yarl.URL(self.url.unicode_string())
            match url.scheme:
                case "redis":
                    url = url.update_query(
                        {
                            "pickle_type": "null",
                            "client_side": "True",
                        }
                    )

            cache.setup(
                settings_url=url.human_repr(),
                suppress=__debug__,
            )

    cache: Cache = Cache()

    class Ecies(BaseModel):
        key: SecretStr | None = None

        # noinspection PyNestedDecorators
        @field_validator("key")
        @classmethod
        def key_validator(cls, v: SecretStr) -> SecretStr:
            if v and not fullmatch(r"[A-Za-z0-9_-]{43}", v.get_secret_value()):
                raise SettingsError(r"Ключ не соответствует формату [A-Za-z0-9_-]{43}")
            return v

        @computed_field
        def bytes(self) -> SecretBytes | None:
            if self.key:
                return SecretBytes(urlsafe_b64decode(self.key.get_secret_value() + "="))
            return None

        @computed_field
        def enabled(self) -> bool:
            return bool(self.key)

    ecies: Ecies = Ecies()

    class AppPath(BaseModel):
        @computed_field
        def root(self) -> Path:
            return Path(__file__).parent.parent.parent

    path: AppPath = AppPath()
