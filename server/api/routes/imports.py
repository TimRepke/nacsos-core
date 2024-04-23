from fastapi import APIRouter, Depends

from nacsos_data.models.imports import ImportModel
from nacsos_data.db.crud.imports import (
    read_import,
    upsert_import,
    delete_import,
    read_all_imports_for_project,
    read_item_count_for_import
)

from server.pipelines import tasks
from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions, InsufficientPermissions
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.imports')
router = APIRouter()

logger.info('Setting up imports route')


@router.get('/list', response_model=list[ImportModel])
async def get_all_imports_for_project(permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) \
        -> list[ImportModel]:
    return await read_all_imports_for_project(project_id=permissions.permissions.project_id,
                                              engine=db_engine)


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
    return await read_item_count_for_import(import_id=import_id, engine=db_engine)


@router.put('/import', response_model=str)
async def put_import_details(import_details: ImportModel,
                             permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))) -> str:
    if str(import_details.project_id) == str(permissions.permissions.project_id):
        logger.debug(import_details)
        key = await upsert_import(import_model=import_details, engine=db_engine)
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
        await delete_import(import_id=import_id, engine=db_engine)
        return str(import_id)

    raise InsufficientPermissions('You do not have permission to delete this data import.')
