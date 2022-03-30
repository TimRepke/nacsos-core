import logging

logger = logging.getLogger('data.db')


def init_db(app):
    @app.on_event("startup")
    async def startup():
        # await database.connect()
        logger.debug('Database connected')

    @app.on_event("shutdown")
    async def shutdown():
        # await database.disconnect()
        logger.debug('Database connection closed')
