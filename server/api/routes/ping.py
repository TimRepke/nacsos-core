from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from sqlalchemy import select, func as F
from nacsos_data.db.schemas.projects import Project

from server.pipelines import tasks
from server.util.logging import get_logger
from server.util.security import InsufficientPermissions
from server.data import db_engine

logger = get_logger('nacsos.api.route.ping')
router = APIRouter()

logger.debug('Setup nacsos.api.route.ping router')


@router.get('/tracked-sleep-task')
async def tracked_task(sleep_time: int = 10):
    tasks.sleepy.tracked_sleep_task.send(sleep_time=sleep_time,  # type: ignore[call-arg]
                                         project_id='86a4d535-0311-41f7-a934-e4ab0a465a68',
                                         comment='Pinged sleeping task')


@router.get('/sleep-task')
async def task(sleep_time: int = 10):
    tasks.sleepy.sleep_task.send(sleep_time=sleep_time)  # type: ignore[call-arg]


@router.get('/', response_class=PlainTextResponse)
async def _pong():
    print('test')
    logger.debug('ping test DEBUG log')
    logger.info('ping test INFO log')
    logger.warning('ping test WARNING log')
    logger.error('ping test ERROR log')
    logger.fatal('ping test FATAL log')
    return 'pong'


class ExampleError(Exception):
    pass


@router.get('/error', response_class=PlainTextResponse)
async def _err() -> str:
    raise ExampleError('Error in your face!')


class ExampleWarning(Warning):
    pass


@router.get('/warn', response_class=PlainTextResponse)
async def _warn() -> str:
    raise ExampleWarning('Warning in your face!')


@router.get('/permission')
async def perm():
    raise InsufficientPermissions('You do not have permission to edit this data import.')


@router.get('/database')
async def db_test():
    async with db_engine.engine.connect() as session:
        rslt = (await session.execute(select(F.count(Project.project_id)))).scalar()
        logger.debug(f'There are {rslt:,} projects on the platform')
        await session.close()
        return rslt


@router.post('/{name}', response_class=PlainTextResponse)
async def _ping(name: str) -> str:
    return f'Hello {name}'
