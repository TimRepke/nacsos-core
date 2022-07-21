from fastapi import APIRouter, Depends, HTTPException, status as http_status

from nacsos_data.models.imports import ImportModel
from nacsos_data.db.crud.imports import \
    read_all_imports_for_project, \
    read_import, upsert_import, \
    read_item_count_for_import

from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions
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
    import_details = await read_import(import_id=import_id,
                                       engine=db_engine)
    if import_details.project_id == permissions.permissions.project_id:
        return import_details

    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail='You do not have permission to access this information.')


@router.get('/import/{import_id}/count/', response_model=int)
async def get_import_details(import_id: str,
                             permissions: UserPermissions = Depends(UserPermissionChecker('imports_read'))) -> int:
    return await read_item_count_for_import(import_id=import_id, engine=db_engine)


@router.put('/import', response_model=str)
async def put_import_details(import_details: ImportModel,
                             permissions: UserPermissions = Depends(UserPermissionChecker('imports_edit'))) -> str:
    if import_details.project_id == permissions.permissions.project_id:
        key = await upsert_import(import_model=import_details, engine=db_engine)
        return str(key)

    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail='You do not have permission to edit this data import.')
