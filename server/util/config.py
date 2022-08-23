from typing import Any
import secrets
import json
import yaml
import os

from pydantic import BaseSettings, BaseModel, PostgresDsn, AnyHttpUrl, EmailStr, validator


# For more information how BaseSettings work, check the documentation:
# https://pydantic-docs.helpmanual.io/usage/settings/

# This is inspired by
# https://github.com/tiangolo/full-stack-fastapi-postgresql/blob/490c554e23343eec0736b06e59b2108fdd057fdc/%7B%7Bcookiecutter.project_slug%7D%7D/backend/app/app/core/config.py


class ServerConfig(BaseModel):
    HOST: str = 'localhost'  # host to run this server on
    PORT: int = 8080  # port for this serve to listen at
    DEBUG_MODE: bool = False  # set this to true in order to get more detailed logs
    WORKERS: int = 2  # number of worker processes
    STATIC_FILES: str = '../nacsos-web/dist/'  # path to the static files to be served

    HASH_ALGORITHM: str = 'HS256'
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # = 8 days

    HEADER_CORS: bool = False  # set to true to allow CORS
    HEADER_TRUSTED_HOST: bool = False  # set to true to allow hosts from any origin
    CORS_ORIGINS: list[AnyHttpUrl] = []  # list of trusted hosts

    @validator("CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str) and not v.startswith('['):
            return [i.strip() for i in v.split(',')]
        if isinstance(v, str) and v.startswith('['):
            ret = json.loads(v)
            if type(ret) == list:
                return ret
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)


class DatabaseConfig(BaseModel):
    HOST: str = 'localhost'  # host of the db server
    PORT: int = 5432  # port of the db server
    USER: str = 'nacsos'  # username for the database
    PASSWORD: str = 'secr€t_passvvord'  # password for the database user
    DATABASE: str = 'nacsos_core'  # name of the database

    CONNECTION_STR: PostgresDsn | None = None

    @validator('CONNECTION_STR', pre=True)
    def build_connection_string(cls, v: str | None, values: dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return PostgresDsn.build(
            scheme="postgresql",
            user=values.get('USER'),
            password=values.get('PASSWORD'),
            host=values.get('HOST'),
            path=f'/{values.get("DATABASE", "")}',
        )


class EmailConfig(BaseModel):
    SMTP_TLS: bool = True
    SMTP_PORT: int | None = None
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SENDER_ADDRESS: EmailStr | None = None
    SENDER_NAME: str | None = 'NACSOS'
    ENABLED: bool = False

    @validator("ENABLED", pre=True)
    def get_emails_enabled(cls, v: bool, values: dict[str, Any]) -> bool:
        return bool(
            values.get('SMTP_HOST')
            and values.get('SMTP_PORT')
            and values.get('SENDER_ADDRESS')
        )

    TEST_USER: EmailStr = EmailStr('test@nacsos.eu')


class UsersConfig(BaseModel):
    # Set a valid user_id to skip authentication
    # (should only be done in dev environment)
    DEFAULT_USER: str | None = None
    REGISTRATION_ENABLED: bool = False  # Set this to true to enable the registration endpoint


class PipelinesConfig(BaseModel):
    API_URL: str = 'http://localhost:8000/api'


class Settings(BaseSettings):
    SERVER: ServerConfig = ServerConfig()
    DB: DatabaseConfig = DatabaseConfig()
    USERS: UsersConfig = UsersConfig()
    PIPES: PipelinesConfig = PipelinesConfig()

    # EMAIL: EmailConfig

    LOG_CONF_FILE: str = 'config/logging.conf'
    LOGGING_CONF: dict[str, Any] | None = None

    @validator('LOGGING_CONF', pre=True)
    def read_logging_config(cls, v: dict[str, Any] | None, values: dict[str, str]) -> dict[str, Any]:
        if isinstance(v, dict):
            return v
        filename = values.get('LOG_CONF_FILE', cls.LOG_CONF_FILE)
        with open(filename, 'r') as f:
            ret = yaml.safe_load(f.read())
            if type(ret) == dict:
                return ret
        raise ValueError('Logging config invalid!')

    class Config:
        case_sensitive = True
        env_nested_delimiter = '__'


conf_file = os.environ.get('NACSOS_CONFIG', 'config/default.env')
settings = Settings(_env_file=conf_file, _env_file_encoding='utf-8')

__all__ = ['settings']
