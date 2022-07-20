from typing import Any
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from nacsos_data.models.users import UserModel
from nacsos_data.models.items import ItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items import \
    read_all_items_for_project, \
    create_item, \
    create_items, \
    read_item_count_for_project
from nacsos_data.db.crud.items.twitter import \
    read_tweet_by_item_id, \
    read_tweet_by_twitter_id, \
    read_tweets_by_author_id, \
    read_paged_tweets_for_project, \
    read_all_tweets_for_project, \
    create_tweet, \
    create_tweets

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.data')
router = APIRouter()

logger.info('Setting up data route')


@router.get('/list/items', response_model=list[ItemModel])
async def list_project_items(project_id: str, permission=Depends(UserPermissionChecker('dataset_read'))):
    items = await read_all_items_for_project(project_id=project_id, engine=db_engine)
    return items


@router.get('/count', response_model=int)
async def count_project_items(project_id: str, permission=Depends(UserPermissionChecker('dataset_read'))) -> int:
    tweets = await read_item_count_for_project(project_id=project_id, engine=db_engine)
    return tweets


@router.get('/detail/{item_id}', response_model=TwitterItemModel)
async def get_detail_for_item(item_id: str, permission=Depends(UserPermissionChecker('dataset_read'))):
    # TODO first check what the correct data format for the project is via Project.type
    tweets = await read_tweet_by_item_id(item_id=item_id, engine=db_engine)
    return tweets


@router.get('/twitter/list', response_model=list[TwitterItemModel])
async def list_project_tweets(project_id: str, permission=Depends(UserPermissionChecker('dataset_read'))):
    tweets = await read_all_tweets_for_project(project_id=project_id, engine=db_engine)
    return tweets


@router.get('/twitter/list/{page}/{page_size}', response_model=list[TwitterItemModel])
async def list_paged_project_tweets(project_id: str, page: int, page_size: int,
                                    permission=Depends(UserPermissionChecker('dataset_read'))):
    tweets = await read_paged_tweets_for_project(project_id=project_id,
                                                 page=page, page_size=page_size,
                                                 engine=db_engine)
    return tweets


# page: count starts at 1


@router.post('/twitter/add')
async def add_tweet(project_id: str, tweet: TwitterItemModel,
                    permission=Depends(UserPermissionChecker('dataset_edit'))):
    return await create_tweet(tweet=tweet, project_id=project_id, engine=db_engine)
