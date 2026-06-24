from typing import Any

from nacsos_data.db import DatabaseEngineAsync
from ..util.config import settings

kw_engine: dict[str, Any] | None = None

if settings.DB.STMT_TIMEOUT > 0:
    kw_engine = {'connect_args': {'options': f'-c statement_timeout={settings.DB.STMT_TIMEOUT}'}}

db_engine = DatabaseEngineAsync(
    host=settings.DB.HOST,
    port=settings.DB.PORT,
    user=settings.DB.USER,
    password=settings.DB.PASSWORD,
    database=settings.DB.DATABASE,
    debug=settings.SERVER.DEBUG_MODE,
    kw_engine=kw_engine,
)
