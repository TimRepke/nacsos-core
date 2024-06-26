import asyncio
import logging
import dramatiq
from dramatiq import get_logger
from dramatiq.middleware import CurrentMessage

from ..actor import NacsosActor


@dramatiq.actor(actor_class=NacsosActor)  # type: ignore[arg-type]
async def tracked_sleep_task(sleep_time: int = 10) -> None:
    message = CurrentMessage.get_current_message()
    print('message')
    print(message)
    log = get_logger('mod', __name__)
    log.info('message')
    log.info(message)
    async with NacsosActor.exec_context() as (session, logger, target_dir, work_dir, task_id, message_id):
        logger.info('Preparing sleep task!')
        await asyncio.sleep(sleep_time)
        logger.info('Done, yo!')


@dramatiq.actor(queue_name='nacsos-pipes')  # type: ignore[arg-type]
async def sleep_task(sleep_time: int = 10) -> None:
    cm = CurrentMessage.get_current_message()
    logger = logging.getLogger('sleepy')
    logger.info('Preparing sleep task!')
    logger.info(f'{cm}')
    logger.info(f'{cm.message_id}')  # type: ignore[union-attr]
    await asyncio.sleep(sleep_time)
    logger.info('Done, yo!')
