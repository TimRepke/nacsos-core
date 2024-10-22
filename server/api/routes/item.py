import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from nacsos_data.db.schemas import AcademicItemVariant, AcademicItem, Annotation, BotAnnotation
from nacsos_data.models.annotations import AnnotationModel
from nacsos_data.models.bot_annotations import BotAnnotationModel
from nacsos_data.models.items import AcademicItemVariantModel, AcademicItemModel
from nacsos_data.util.academic.duplicate import str_to_title_slug
from nacsos_data.util.errors import MissingIdError
from nacsos_data.util.auth import UserPermissions

from server.models import ImportM2M
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

logger = get_logger('nacsos.api.route.item')
router = APIRouter()

logger.info('Setting up items route')


@router.get('/variants/{item_id}', response_model=list[AcademicItemVariantModel])
async def get_item_variants(item_id: str,
                            permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) \
        -> list[AcademicItemVariantModel]:
    # FIXME: generalise this to beyond AcademicItemModel
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = (
            await session.execute(sa.select(AcademicItemVariant)
                                  .where(AcademicItemVariant.item_id == item_id))
        ).scalars().all()
        return [AcademicItemVariantModel(**r.__dict__) for r in rslt]


@router.get('/info/{item_id}', response_model=AcademicItemModel)
async def get_item_info(item_id: str,
                        permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) \
        -> AcademicItemModel:
    # FIXME: generalise this to beyond AcademicItemModel
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = await session.scalar(sa.select(AcademicItem).where(AcademicItem.item_id == item_id))
        if rslt is None:
            raise MissingIdError('No item with this ID.')
        return AcademicItemModel(**rslt.__dict__)


@router.get('/m2ms/{item_id}', response_model=list[ImportM2M])
async def get_item_m2ms(item_id: str,
                        permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) \
        -> list[ImportM2M]:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = (await session.execute(sa.text('SELECT * FROM m2m_import_item WHERE item_id=:import_id'),
                                      {'import_id': item_id})).mappings().all()
        return [ImportM2M(**r) for r in rslt]


@router.get('/labels/{item_id}', response_model=tuple[list[AnnotationModel], list[BotAnnotationModel]])
async def get_item_labels(item_id: str,
                          permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) \
        -> tuple[list[AnnotationModel], list[BotAnnotationModel]]:
    async with db_engine.session() as session:  # type: AsyncSession
        set_1 = (await session.execute(sa.select(Annotation)
                                       .where(Annotation.item_id == item_id))).scalars().all()
        set_2 = (await session.execute(sa.select(BotAnnotation)
                                       .where(BotAnnotation.item_id == item_id))).scalars().all()

        return [AnnotationModel(**a.__dict__) for a in set_1], [BotAnnotationModel(**ba.__dict__) for ba in set_2]


@router.put('/info')
async def update_item_info(item: AcademicItemModel,
                           permissions: UserPermissions = Depends(UserPermissionChecker('dataset_edit'))) \
        -> None:
    # FIXME: generalise this to beyond AcademicItemModel
    async with db_engine.session() as session:  # type: AsyncSession
        orm = await session.scalar(sa.select(AcademicItem)
                                   .where(AcademicItem.item_id == item.item_id,
                                          AcademicItem.project_id == permissions.permissions.project_id))
        if orm is None:
            raise MissingIdError('No item with this ID.')

        for key in item.model_fields_set:
            setattr(orm, key, getattr(item, key))

        orm.title_slug = str_to_title_slug(item.title)
        orm.time_edited = datetime.datetime.now()

        await session.commit()
