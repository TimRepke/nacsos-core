from fastapi import APIRouter, Depends, HTTPException, status, Query
from nacsos_data.db.schemas import Project

from nacsos_data.models.projects import ProjectTypeLiteral
from nacsos_data.models.items import AnyItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items import \
    read_item_count_for_project
from nacsos_data.db.crud.items.basic import \
    read_all_basic_items_for_project, \
    read_all_basic_items_for_project_paged, \
    read_basic_item_by_item_id
from nacsos_data.db.crud.items.twitter import \
    read_all_twitter_items_for_project, \
    read_all_twitter_items_for_project_paged, \
    read_twitter_item_by_item_id, \
    create_twitter_item

from server.api.errors import ItemNotFoundError, NoDataForKeyError
from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.data')
router = APIRouter()

logger.info('Setting up data route')


@router.get('/{item_type}/list', response_model=list[AnyItemModel])
async def list_project_data(item_type: ProjectTypeLiteral,
                            permission=Depends(UserPermissionChecker('dataset_read'))):
    project_id = permission.permissions.project_id
    if item_type == 'basic':
        return await read_all_basic_items_for_project(project_id=project_id, engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project(project_id=project_id, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=f'Data listing for {item_type} not implemented (yet).')


@router.get('/{item_type}/list/{page}/{page_size}', response_model=list[AnyItemModel])
async def list_project_data_paged(item_type: ProjectTypeLiteral, page: int, page_size: int,
                                  permission=Depends(UserPermissionChecker('dataset_read'))):
    project_id = permission.permissions.project_id
    if item_type == 'basic':
        return await read_all_basic_items_for_project_paged(project_id=project_id,
                                                            page=page, page_size=page_size, engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project_paged(project_id=project_id,
                                                              page=page, page_size=page_size, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=f'Paged data listing for {item_type} not implemented (yet).')


@router.get('/detail/{item_id}', response_model=AnyItemModel)
async def get_detail_for_item(item_id: str,
                              item_type: ProjectTypeLiteral | None = Query(default=None),
                              permission=Depends(UserPermissionChecker('dataset_read'))) -> AnyItemModel:
    if item_type is None:
        async with db_engine.session() as session:
            project: Project | None = await session.get(Project, permission.permissions.project_id)
            assert project is not None
            item_type = project.type

    result: AnyItemModel | None = None
    if item_type == 'basic':
        result = await read_basic_item_by_item_id(item_id=item_id, engine=db_engine)
    elif item_type == 'twitter':
        result = await read_twitter_item_by_item_id(item_id=item_id, engine=db_engine)
    else:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                            detail=f'Detail getter for {item_type} not implemented (yet).')

    if result is not None:
        return result
    raise ItemNotFoundError(f'No item found with type {item_type} with the id {item_id}')


@router.get('/count', response_model=int)
async def count_project_items(permission=Depends(UserPermissionChecker('dataset_read'))) -> int:
    tweets = await read_item_count_for_project(project_id=permission.permissions.project_id, engine=db_engine)
    return tweets


@router.post('/twitter/add')
async def add_tweet(tweet: TwitterItemModel,
                    permission=Depends(UserPermissionChecker('dataset_edit'))):
    return await create_twitter_item(tweet=tweet, project_id=permission.permissions.project_id, engine=db_engine)
