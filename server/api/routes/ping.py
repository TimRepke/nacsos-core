from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
import logging

logger = logging.getLogger('nacsos.api.route.ping')
router = APIRouter()

logger.debug('Setup nacsos.api.route.ping router')


@router.get('/', response_class=PlainTextResponse)
async def _pong():
    logger.debug('ping test DEBUG log')
    logger.info('ping test INFO log')
    logger.warning('ping test WARNING log')
    logger.error('ping test ERROR log')
    logger.fatal('ping test FATAL log')
    return 'pong'


@router.post('/{name}', response_class=PlainTextResponse)
async def _ping(name: str) -> str:
    return f'Hello {name}'
