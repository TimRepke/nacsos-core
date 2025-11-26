from nacsos_data.db import DatabaseEngineAsync
from ..util.config import settings

db_engine = DatabaseEngineAsync(
    host=settings.DB.HOST,
    port=settings.DB.PORT,
    user=settings.DB.USER,
    password=settings.DB.PASSWORD,
    database=settings.DB.DATABASE,
    debug=settings.SERVER.DEBUG_MODE,
)
