from fastapi import APIRouter, Depends
import sqlalchemy as sa
from nacsos_data.db.schemas import Task
from nacsos_data.db.schemas.imports import ImportRevision, Import
from nacsos_data.models.imports import ImportModel, ImportRevisionModel
from nacsos_data.db.crud.imports import (
    read_import,
    upsert_import,
    delete_import,
    read_item_count_for_import
)
from nacsos_data.models.pipeline import TaskModel
from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

from server.pipelines import tasks
from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions, InsufficientPermissions
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.imports')
router = APIRouter()

logger.info('Setting up imports route')


class ImportInfo(ImportModel):
    num_revisions: int
    num_items: int | None = None


class ImportRevisionDetails(ImportRevisionModel):
    task: TaskModel | None = None


class ImportDetails(ImportModel):
    revisions: list[ImportRevisionModel]


@router.get('/list', response_model=list[ImportInfo])
async def get_all_imports_for_project(permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) \
        -> list[ImportInfo]:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = await session.execute(sa.text('SELECT im.*, '
                                             '       count(ir.import_revision_counter) as num_revisions, '
                                             '       max(ir.num_items) as num_items '
                                             'FROM import im '
                                             'LEFT OUTER JOIN import_revision ir ON im.import_id = ir.import_id '
                                             'WHERE im.project_id = :project_id '
                                             'GROUP BY im.import_id;'),
                                     {'project_id': permissions.permissions.project_id})

        return [ImportInfo(**ii) for ii in rslt.mappings().all()]


@router.get('/list/details', response_model=list[ImportDetails])
async def get_project_imports(permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) \
        -> list[ImportDetails]:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = (await session.execute(
            sa.select(Import,
                      sa.func.array_agg(
                          sa.func.row_to_json(ImportRevision.__table__.table_valued())  # type: ignore[attr-defined]
                      ).label('revisions'))
            .join(ImportRevision, ImportRevision.import_id == Import.import_id, isouter=True)
            .where(Import.project_id == permissions.permissions.project_id)
            .group_by(Import.import_id))).mappings().all()
        return [
            ImportDetails(
                **ii['Import'].__dict__,
                revisions=(
                    []
                    if not ii['revisions'] else
                    [
                        ImportRevisionModel(**rev)
                        for rev in ii['revisions']
                        if rev is not None
                    ]
                )
            )
            for ii in rslt
        ]


@router.get('/import/{import_id}', response_model=ImportModel)
async def get_import_details(import_id: str,
                             permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) \
        -> ImportModel:
    import_details = await read_import(import_id=import_id, engine=db_engine)
    if import_details is not None and str(import_details.project_id) == str(permissions.permissions.project_id):
        return import_details

    raise InsufficientPermissions('You do not have permission to access this information.')


@router.get('/import/{import_id}/count/', response_model=int)
async def get_import_counts(import_id: str,
                            permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) -> int:
    # FIXME: add permission check for project_id
    return await read_item_count_for_import(import_id=import_id, engine=db_engine)


@router.get('/import/{import_id}/revisions', response_model=list[ImportRevisionDetails])
async def get_import_revisions(import_id: str,
                               permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) \
        -> list[ImportRevisionDetails]:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = await session.execute(
            sa.select(ImportRevision, Task)
            .join(Task, sa.func.cast(Task.task_id, sa.Text) == ImportRevision.pipeline_task_id, isouter=True)
            .where(ImportRevision.import_id == import_id)
            .order_by(sa.desc(ImportRevision.import_revision_counter)))
        # FIXME: add permission check for project_id
        return [
            ImportRevisionDetails(**ird['ImportRevision'].__dict__,
                                  task=None if not ird['Task'] else ird['Task'].__dict__)
            for ird in rslt.mappings().all()
        ]


@router.put('/import', response_model=str)
async def put_import_details(import_details: ImportModel,
                             permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))) -> str:
    if str(import_details.project_id) == str(permissions.permissions.project_id):
        logger.debug(import_details)
        key = await upsert_import(import_model=import_details, engine=db_engine, use_commit=True)
        return str(key)

    raise InsufficientPermissions('You do not have permission to edit this data import.')


@router.post('/import/{import_id}')
async def trigger_import(import_id: str,
                         permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))) -> None:
    import_details = await read_import(import_id=import_id, engine=db_engine)
    if import_details is not None and str(import_details.project_id) == str(permissions.permissions.project_id):
        tasks.imports.import_task.send(project_id=str(import_details.project_id),  # type: ignore[call-arg]
                                       user_id=str(permissions.user.user_id),
                                       comment=f'Import for "{import_details.name}" ({import_id})',
                                       import_id=import_id)
    else:
        raise InsufficientPermissions('You do not have permission to edit this data import.')


@router.delete('/import/delete/{import_id}', response_model=str)
async def delete_import_details(import_id: str,
                                permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))):
    import_details = await read_import(import_id=import_id, engine=db_engine)

    # First, make sure the user trying to delete this import is actually authorised to delete this specific import
    if import_details is not None and str(import_details.project_id) == str(permissions.permissions.project_id):
        await delete_import(import_id=import_id, engine=db_engine, use_commit=True)
        return str(import_id)

    raise InsufficientPermissions('You do not have permission to delete this data import.')
