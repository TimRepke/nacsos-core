#!/usr/bin/env python3


def run(args=None):
    import logging

    from server.util.log import init_logging
    init_logging()

    logger = logging.getLogger('nacsos.main')
    logger.info('Starting up uvicorn')

    # this should be imported here to ensure config gets initialised first
    from server.util.config import conf

    # import asyncio
    # from hypercorn.config import Config
    # from hypercorn.asyncio import serve

    import uvicorn
    from server.api.server import Server
    from server.data.database import init_db

    server = Server()

    uvicorn.run(server.app, host=conf.server.host, port=conf.server.port)

    init_db(server.app)

    return server.app

if __name__ == '__main__':
    run()
