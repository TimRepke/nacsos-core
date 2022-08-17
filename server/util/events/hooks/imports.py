from datetime import datetime

from sqlalchemy import select
from nacsos_data.db.schemas import Import

from ..events import PipelineTaskStatusChangedEvent
from ....data import db_engine


async def update_import_status(event: PipelineTaskStatusChangedEvent):
    if event.function_name in [
        'nacsos_lib.twitter.import.import_twitter_api',
        'nacsos_lib.twitter.import.import_twitter_db'
    ]:
        async with db_engine.session() as session:
            stmt = select(Import).filter_by(pipeline_task_id=event.task_id)
            import_details: Import = (await session.execute(stmt)).scalars().one_or_none()
            if import_details is not None:

                # Seems like task was started, remember the time
                if event.status == 'RUNNING' and import_details.time_started is None:
                    import_details.time_started = datetime.now()
                    session.commit()

                elif (event.status == 'COMPLETED' or event.status == 'FAILED' or event.status == 'CANCELLED') \
                        and import_details.time_finished is None:
                    import_details.time_finished = datetime.now()
                    session.commit()
