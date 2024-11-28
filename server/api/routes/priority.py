from typing import Any, TYPE_CHECKING, TypedDict
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy import select, delete, text
from sqlalchemy.dialects import postgresql as psa
import numpy as np

from nacsos_data.db.schemas.priority import Priority
from nacsos_data.models.priority import PriorityModel, DehydratedPriorityModel
from nacsos_data.util.annotations.export import wide_export_table
from nacsos_data.util.priority.mask import get_inclusion_mask

from fastapi.responses import FileResponse

from nacsos_data.util.nql import NQLFilter
from server.util.config import settings
from server.util.files import get_outputs_flat
from server.util.security import (
    UserPermissionChecker,
    UserPermissions,
    UserPriorityPermissions,
    UserPriorityPermissionChecker
)
from server.util.logging import get_logger
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
    import pandas as pd  # noqa: F401

logger = get_logger('nacsos.api.route.priority')

router = APIRouter()


class PrioTableParams(BaseModel):
    scope_ids: list[str]

    incl: str = 'incl:1'
    query: NQLFilter | None = None

    limit: int = 20


async def _get_df(project_id: str,
                  scope_ids: list[str],
                  incl: str,
                  query: NQLFilter | None = None,
                  limit: int | None = 20) -> tuple[int, int, int, 'pd.DataFrame']:
    async with db_engine.session() as session:  # type: AsyncSession
        base_cols, label_cols, df = await wide_export_table(session=session,
                                                            project_id=project_id,
                                                            nql_filter=query,
                                                            scope_ids=scope_ids,
                                                            limit=limit)
        try:
            df['incl'] = get_inclusion_mask(rule=incl, df=df, label_cols=label_cols)
        except KeyError:
            df['incl'] = 'ERROR'
        return df.shape[0], (df['incl'] is True).sum(), (df['incl'] is False).sum(), df


@router.post('/table/peek/html', response_model=str)
async def get_table_sample_html(params: PrioTableParams,
                                permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> Any:
    _, _, _, df = await _get_df(project_id=str(permissions.permissions.project_id),
                                scope_ids=params.scope_ids,
                                incl=params.incl,
                                query=params.query,
                                limit=min(params.limit, 500))
    return df.drop(columns=['text']).replace({np.nan: None}).replace({None: np.nan}).to_html(na_rep='')


class SampleResponse(BaseModel):
    data: list[dict[str, Any]]
    n_total: int
    n_incl: int
    n_excl: int


@router.post('/table/peek', response_model=SampleResponse)
async def get_table_sample(
        params: PrioTableParams,
        permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))
) -> Any:
    n_total, n_incl, n_excl, df = await _get_df(project_id=str(permissions.permissions.project_id),
                                                scope_ids=params.scope_ids,
                                                incl=params.incl,
                                                query=params.query,
                                                limit=min(params.limit, 500))
    return SampleResponse(data=df.drop(columns=['text']).to_dict(orient='records'),
                          n_total=n_total, n_incl=n_incl, n_excl=n_excl)


@router.get('/setups', response_model=list[DehydratedPriorityModel])
async def read_project_setups(
        permissions: UserPermissions = Depends(UserPermissionChecker('annotations_prio'))
) -> list[DehydratedPriorityModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = (await session.execute(text('''
        SELECT priority_id, project_id, name,
               time_created, time_ready, time_started, time_assigned,
               array_length(prioritised_ids, 1) as num_prioritised
        FROM priorities
        WHERE project_id = :project_id;
        '''), {'project_id': permissions.permissions.project_id})).mappings().all()
        return [DehydratedPriorityModel(**r) for r in rslt]


@router.get('/setup', response_model=PriorityModel | None)
async def read_prio_setup(
        priority_id: str,
        permissions: UserPermissions = Depends(UserPermissionChecker('annotations_prio'))
) -> PriorityModel | None:
    async with db_engine.session() as session:  # type: AsyncSession
        rslt = await session.scalar(select(Priority).where(Priority.priority_id == priority_id))
        if rslt:
            return PriorityModel(**rslt.__dict__)
        return None


@router.put('/setup')
async def save_prio_setup(config: PriorityModel,
                          permissions: UserPermissions = Depends(UserPermissionChecker('annotations_prio'))) -> None:
    async with db_engine.session() as session:  # type: AsyncSession
        await session.execute(
            psa
            .insert(Priority)
            .values(**config.model_dump(exclude_unset=True))
            .on_conflict_do_update(
                constraint='priorities_pkey',
                set_=config.model_dump(exclude={'priority_id'})
            ))
        await session.commit()


@router.delete('/setup')
async def drop_prio_setup(priority_id: str,
                          permissions: UserPermissions = Depends(UserPermissionChecker('annotations_prio'))) -> None:
    async with db_engine.session() as session:  # type: AsyncSession
        await session.execute(delete(Priority).where(Priority.priority_id == priority_id))
        await session.commit()


class FileOnDisk(TypedDict):
    path: str
    size: int


@router.get('/artefacts/list', response_model=list[FileOnDisk])
def get_artefacts(
        permissions: UserPriorityPermissions = Depends(UserPriorityPermissionChecker('artefacts_read'))
) -> list[FileOnDisk]:
    priority_id = str(permissions.priority.priority_id)

    return [
        FileOnDisk(path=file[0],
                   size=file[1])  # type: ignore[typeddict-item]
        for file in get_outputs_flat(root=settings.PIPES.priority_dir / priority_id,
                                     base=settings.PIPES.priority_dir,
                                     include_fsize=True)
    ]


@router.get('/artefacts/file', response_class=FileResponse)
def get_file(filename: str,
             permissions: UserPriorityPermissions = Depends(UserPriorityPermissionChecker('artefacts_read'))) \
        -> FileResponse:
    return FileResponse(settings.PIPES.priority_dir / filename)
