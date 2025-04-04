from pathlib import Path
from typing import Any
import secrets
import json
import toml
import os

from pydantic_settings import SettingsConfigDict, BaseSettings
from pydantic.networks import PostgresDsn
from pydantic import field_validator, ValidationInfo, AnyHttpUrl, BaseModel, model_validator


# For more information how BaseSettings work, check the documentation:
# https://pydantic-docs.helpmanual.io/usage/settings/

# This is inspired by
# https://github.com/tiangolo/full-stack-fastapi-postgresql/blob/490c554e23343eec0736b06e59b2108fdd057fdc/%7B%7Bcookiecutter.project_slug%7D%7D/backend/app/app/core/config.py


class ServerConfig(BaseModel):
    HOST: str = 'localhost'  # host to run this server on
    PORT: int = 8080  # port for this serve to listen at
    DEBUG_MODE: bool = False  # set this to true in order to get more detailed logs
    WORKERS: int = 2  # number of worker processes
    WEB_URL: str = 'https://localhost'  # URL to the web frontend (without trailing /)
    STATIC_FILES: str = '../nacsos-web/dist/'  # path to the static files to be served
    OPENAPI_FILE: str = '/openapi.json'  # absolute URL path to openapi.json file
    OPENAPI_PREFIX: str = ''  # see https://fastapi.tiangolo.com/advanced/behind-a-proxy/
    ROOT_PATH: str = ''  # see https://fastapi.tiangolo.com/advanced/behind-a-proxy/

    HASH_ALGORITHM: str = 'HS256'
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # = 8 days

    HEADER_CORS: bool = False  # set to true to allow CORS
    HEADER_TRUSTED_HOST: bool = False  # set to true to allow hosts from any origin
    CORS_ORIGINS: list[str] = []  # list of trusted hosts

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str) and not v.startswith('['):
            return [i.strip() for i in v.split(',')]
        if isinstance(v, str) and v.startswith('['):
            ret = json.loads(v)
            if type(ret) is list:
                return ret
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)


class DatabaseConfig(BaseModel):
    SCHEME: str = 'postgresql'
    SCHEMA: str = 'public'
    HOST: str = 'localhost'  # host of the db server
    PORT: int = 5432  # port of the db server
    USER: str = 'nacsos'  # username for the database
    PASSWORD: str = 'secr€t_passvvord'  # password for the database user
    DATABASE: str = 'nacsos_core'  # name of the database

    CONNECTION_STR: PostgresDsn | None = None

    @field_validator('CONNECTION_STR', mode='before')
    def build_connection_string(cls, v: str | None, info: ValidationInfo) -> PostgresDsn:
        assert info.config is not None

        if isinstance(v, str):
            raise ValueError('This field will be generated automatically, please do not use it.')

        return PostgresDsn.build(
            scheme=info.data.get('SCHEME', 'postgresql'),
            username=info.data.get('USER'),
            password=info.data.get('PASSWORD'),
            host=info.data.get('HOST'),
            port=info.data.get('PORT'),
            path=f'/{info.data.get("DATABASE", "")}',
        )


class EmailConfig(BaseModel):
    ENABLED: bool = False
    SMTP_TLS: bool = True
    SMTP_START_TLS: bool | None = None
    SMTP_CHECK_CERT: bool = True
    SMTP_PORT: int | None = None
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SENDER: str | None = 'NACSOS <noreply@mcc-berlin.net>'
    ADMINS: list[str] | None = None


class UsersConfig(BaseModel):
    # Set a valid user_id to skip authentication
    # (should only be done in dev environment)
    DEFAULT_USER: str | None = None
    REGISTRATION_ENABLED: bool = False  # Set this to true to enable the registration endpoint


class PipelinesConfig(BaseModel):
    TOKEN: str = ''
    USERNAME: str | None = None
    USER_ID: str | None = None
    REDIS_URL: str = 'redis://localhost:6379/0'

    DATA_PATH: Path = Path('.tasks')  # Where results and the job database will be stored.
    WORKING_DIR: Path = Path('.tasks/tmp')  # Directory for temporary files

    @property
    def target_dir(self) -> Path:
        return (self.DATA_PATH / 'artefacts').resolve()

    @property
    def user_data_dir(self) -> Path:
        return (self.DATA_PATH / 'user_data').resolve()

    @property
    def priority_dir(self) -> Path:
        return (self.DATA_PATH / 'priority').resolve()

    @model_validator(mode='before')
    @classmethod
    def fix_paths(cls, data: Any) -> Any:
        def ensure_path(key: str) -> Path:
            v = data.get(key)
            if isinstance(v, str):
                path = Path(v)
            elif isinstance(v, Path) and v.is_absolute():
                path = v
            elif isinstance(v, Path):
                path = (Path.cwd() / Path(v))
            else:
                raise ValueError(f'Invalid path for {key}: {v}')
            path = path.resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path

        data['DATA_PATH'] = ensure_path('DATA_PATH')
        data['WORKING_DIR'] = ensure_path('WORKING_DIR')
        return data


class Settings(BaseSettings):
    # Basic server hosting settings
    SERVER: ServerConfig
    # Database connection to main database
    DB: DatabaseConfig
    # Global user account settings
    USERS: UsersConfig
    # Settings for the nacsos-pipelines API
    PIPES: PipelinesConfig

    # OpenAlex in PostgreSQL
    OA_DB: DatabaseConfig
    # URL including path to OpenAlex collection
    OA_SOLR: AnyHttpUrl = 'http://localhost:8983/solr/openalex'  # type: ignore[assignment]

    EMAIL: EmailConfig

    LOG_CONF_FILE: str = 'config/logging.toml'
    LOGGING_CONF: dict[str, Any] | None = None

    @field_validator('LOGGING_CONF', mode='before')
    @classmethod
    def get_emails_enabled(cls, v: dict[str, Any] | None, info: ValidationInfo) -> dict[str, Any]:
        assert info.config is not None

        if isinstance(v, dict):
            return v
        filename = info.data.get('LOG_CONF_FILE', None)

        if filename is not None:
            with open(filename, 'r') as f:
                ret = toml.loads(f.read())
                if type(ret) is dict:
                    return ret
        raise ValueError('Logging config invalid!')

    model_config = SettingsConfigDict(case_sensitive=True, env_prefix='NACSOS_', env_nested_delimiter='__')


conf_file = os.environ.get('NACSOS_CONFIG', 'config/default.env')
settings = Settings(_env_file=conf_file, _env_file_encoding='utf-8')  # type: ignore[call-arg]  # FIXME

__all__ = ['settings', 'conf_file']
