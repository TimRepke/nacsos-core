from fastapi import APIRouter, Depends, HTTPException, status

from nacsos_data.models.projects import ProjectTypeLiteral
from nacsos_data.models.items import AnyItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items import \
    create_item, \
    create_items, \
    read_item_count_for_project
from nacsos_data.db.crud.items.basic import \
    read_all_basic_items_for_project, \
    read_all_basic_items_for_project_paged, \
    read_basic_item_by_item_id
from nacsos_data.db.crud.items.twitter import \
    read_all_twitter_items_for_project, \
    read_all_twitter_items_for_project_paged, \
    read_twitter_item_by_item_id, \
    read_twitter_items_by_author_id, \
    read_twitter_item_by_twitter_id, \
    create_twitter_item, \
    create_twitter_items

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.data')
router = APIRouter()

logger.info('Setting up data route')


@router.get('/{item_type}/list', response_model=list[AnyItemModel])
async def list_project_data(project_id: str, item_type: ProjectTypeLiteral,
                            permission=Depends(UserPermissionChecker('dataset_read'))):
    if item_type == 'basic':
        return await read_all_basic_items_for_project(project_id=project_id, engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project(project_id=project_id, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=f'Data listing for {item_type} not implemented (yet).')


@router.get('/{item_type}/list/{page}/{page_size}', response_model=list[AnyItemModel])
async def list_project_data(project_id: str, item_type: ProjectTypeLiteral, page: int, page_size: int,
                            permission=Depends(UserPermissionChecker('dataset_read'))):
    if item_type == 'basic':
        return await read_all_basic_items_for_project_paged(project_id=project_id,
                                                            page=page, page_size=page_size, engine=db_engine)
    if item_type == 'twitter':
        return await read_all_twitter_items_for_project_paged(project_id=project_id,
                                                              page=page, page_size=page_size, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=f'Paged data listing for {item_type} not implemented (yet).')


@router.get('/{item_type}/detail/{item_id}', response_model=AnyItemModel)
async def get_detail_for_item(item_id: str, item_type: ProjectTypeLiteral,
                              permission=Depends(UserPermissionChecker('dataset_read'))):
    if item_type == 'basic':
        return await read_basic_item_by_item_id(item_id=item_id, engine=db_engine)
    if item_type == 'twitter':
        return await read_twitter_item_by_item_id(item_id=item_id, engine=db_engine)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=f'Detail getter for {item_type} not implemented (yet).')


@router.get('/count', response_model=int)
async def count_project_items(project_id: str, permission=Depends(UserPermissionChecker('dataset_read'))) -> int:
    tweets = await read_item_count_for_project(project_id=project_id, engine=db_engine)
    return tweets


@router.post('/twitter/add')
async def add_tweet(project_id: str, tweet: TwitterItemModel,
                    permission=Depends(UserPermissionChecker('dataset_edit'))):
    return await create_twitter_item(tweet=tweet, project_id=project_id, engine=db_engine)
