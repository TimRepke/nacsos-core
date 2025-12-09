from fastapi import APIRouter, Depends, HTTPException, status, Query
from nacsos_data.db.crud.items.lexis_nexis import read_lexis_paged_for_project
from nacsos_data.db.schemas import Project, ItemTypeLiteral, GenericItem, AcademicItem, ItemType, Item, LexisNexisItem
from nacsos_data.models.items import AnyItemModel, GenericItemModel, AcademicItemModel, AnyItemModelList, LexisNexisItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items import read_item_count_for_project, read_all_for_project, read_paged_for_project, read_any_item_by_item_id
from nacsos_data.db.crud.items.twitter import (
    read_all_twitter_items_for_project,
    read_all_twitter_items_for_project_paged,
    read_twitter_item_by_item_id,
    import_tweet,
)
from nacsos_data.util.auth import UserPermissions
from sqlalchemy import select

from server.api.errors import ItemNotFoundError
from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.data')
router = APIRouter()

logger.info('Setting up data route')


@router.get('/{item_type}/list', response_model=AnyItemModelList)
async def list_project_data(
    item_type: ItemTypeLiteral,
    permission: UserPermissions = Depends(UserPermissionChecker('dataset_read')),
) -> AnyItemModelList:
    project_id = permission.permissions.project_id
    if item_type == 'generic':
        return await read_all_for_project(Model=GenericItemModel, Schema=GenericItem, project_id=project_id, engine=db_engine)
    if item_type == 'academic':
        return await read_all_for_project(Model=AcademicItemModel, Schema=AcademicItem, project_id=project_id, engine=db_engine)
    if item_type == 'lexis':
        return await read_all_for_project(Model=LexisNexisItemModel, Schema=LexisNexisItem, project_id=project_id, engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project(project_id=project_id, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'Data listing for {item_type} not implemented (yet).')


@router.get('/{item_type}/list/{page}/{page_size}', response_model=AnyItemModelList)
async def list_project_data_paged(
    item_type: ItemTypeLiteral,
    page: int,
    page_size: int,
    permission: UserPermissions = Depends(UserPermissionChecker('dataset_read')),
) -> AnyItemModelList:
    project_id = permission.permissions.project_id
    if item_type == 'generic':
        return await read_paged_for_project(Model=GenericItemModel, Schema=GenericItem, page=page, page_size=page_size, project_id=project_id, engine=db_engine)
    if item_type == 'academic':
        return await read_paged_for_project(
            Model=AcademicItemModel, Schema=AcademicItem, page=page, page_size=page_size, project_id=project_id, engine=db_engine
        )
    if item_type == 'lexis':
        return await read_lexis_paged_for_project(page=page, page_size=page_size, project_id=project_id, db_engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project_paged(project_id=project_id, page=page, page_size=page_size, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'Paged data listing for {item_type} not implemented (yet).')


@router.get('/detail/{item_id}', response_model=AnyItemModel)
async def get_detail_for_item(
    item_id: str,
    item_type: ItemTypeLiteral | None = Query(default=None),
    permission: UserPermissions = Depends(UserPermissionChecker('dataset_read')),
) -> AnyItemModel:
    if item_type is None:
        async with db_engine.session() as session:
            project: Project | None = await session.get(Project, permission.permissions.project_id)
            assert project is not None
            item_type = project.type

    result: AnyItemModel | None = None
    if item_type == 'generic':
        result = await read_any_item_by_item_id(item_id=item_id, item_type=item_type, engine=db_engine)
    elif item_type == 'twitter':
        result = await read_twitter_item_by_item_id(item_id=item_id, engine=db_engine)
    elif item_type == 'academic':
        result = await read_any_item_by_item_id(item_id=item_id, item_type=ItemType.academic, engine=db_engine)
    elif item_type == 'lexis':
        result = await read_any_item_by_item_id(item_id=item_id, item_type=ItemType.lexis, engine=db_engine)
    else:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'Detail getter for {item_type} not implemented (yet).')

    if result is not None:
        return result
    raise ItemNotFoundError(f'No item found with type {item_type} with the id {item_id}')


@router.get('/text/{item_id}', response_model=str)
async def get_text_for_item(
    item_id: str,
    permission: UserPermissions = Depends(UserPermissionChecker('dataset_read')),
) -> str:
    async with db_engine.session() as session:
        stmt = select(Item.text).where(Item.item_id == item_id)
        text: str | None = await session.scalar(stmt)
        if text is None:
            raise ItemNotFoundError(f'No text available for item with ID: {item_id}')
        return text


@router.get('/count', response_model=int)
async def count_project_items(permission: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> int:
    tweets = await read_item_count_for_project(project_id=permission.permissions.project_id, engine=db_engine)
    return tweets


@router.post('/twitter/add')
async def add_tweet(
    tweet: TwitterItemModel,
    import_id: str | None = None,
    permission: UserPermissions = Depends(UserPermissionChecker('dataset_edit')),
) -> TwitterItemModel:
    return await import_tweet(tweet=tweet, project_id=permission.permissions.project_id, import_id=import_id, engine=db_engine)
