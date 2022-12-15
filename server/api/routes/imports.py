from fastapi import APIRouter, Depends

from nacsos_data.models.imports import ImportModel, ImportType
from nacsos_data.db.crud.imports import \
    read_all_imports_for_project, \
    read_import, \
    upsert_import, \
    read_item_count_for_import
from nacsos_data.util.pipelines.imports import submit_jsonl_import_task, submit_wos_import_task

from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions, InsufficientPermissions
from server.util.logging import get_logger
from server.util.config import settings

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


@router.post('/import/{import_id}', response_model=str)
async def trigger_import(import_id: str,
                         permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))):
    import_details = await read_import(import_id=import_id, engine=db_engine)

    if import_details is not None and str(import_details.project_id) == str(permissions.permissions.project_id):
        if import_details.type == ImportType.jsonl:
            return await submit_jsonl_import_task(import_id=import_id,
                                                  base_url=settings.PIPES.API_URL,
                                                  engine=db_engine)
        elif import_details.type == ImportType.wos:
            return await submit_wos_import_task(import_id=import_id,
                                                base_url=settings.PIPES.API_URL,
                                                engine=db_engine)
        else:
            raise NotImplementedError(f'No import trigger for "{import_details.type}" implemented yet.')
    else:
        raise InsufficientPermissions('You do not have permission to edit this data import.')
