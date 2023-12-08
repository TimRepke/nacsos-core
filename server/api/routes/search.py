from typing import TYPE_CHECKING

import httpx
from nacsos_data.db.crud.items.lexis_nexis import lexis_orm_to_model
from nacsos_data.db.schemas import Project, ItemType
from pydantic import BaseModel
from fastapi import APIRouter, Depends
import sqlalchemy.sql.functions as func

from nacsos_data.util.academic.openalex import query_async, SearchResult
from nacsos_data.db.crud.items import Query
from nacsos_data.db.crud.items.query.parse import GRAMMAR
from nacsos_data.models.items import AcademicItemModel, FullLexisNexisItemModel
from nacsos_data.models.openalex.solr import SearchField, DefType, OpType
from sqlalchemy import select

from server.util.security import UserPermissionChecker, UserPermissions
from server.util.logging import get_logger
from server.util.config import settings
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()

logger = get_logger('nacsos.api.route.search')
logger.info('Setting up academic search route')


class TermStats(BaseModel):
    term: str
    df: int
    ttf: int


@router.post('/openalex/select', response_model=SearchResult)
async def search_openalex(query: str,
                          limit: int = 20,
                          offset: int = 0,
                          def_type: DefType = 'lucene',
                          field: SearchField = 'title_abstract',
                          histogram: bool = False,
                          op: OpType = 'AND',
                          histogram_from: int = 1990,
                          histogram_to: int = 2024,
                          permissions: UserPermissions = Depends(UserPermissionChecker('search_oa'))) -> SearchResult:
    return await query_async(query=query,
                             openalex_endpoint=str(settings.OA_SOLR),
                             histogram=histogram,
                             histogram_to=histogram_to,
                             histogram_from=histogram_from,
                             op=op,
                             def_type=def_type,
                             field=field,
                             offset=offset,
                             limit=limit)


@router.get('/openalex/terms', response_model=list[TermStats])
async def term_expansion(term_prefix: str,
                         limit: int = 20,
                         permissions: UserPermissions = Depends(UserPermissionChecker('search_oa'))) -> list[TermStats]:
    url = f'{settings.OA_SOLR}/terms' \
          f'?facet=true' \
          f'&indent=true' \
          f'&q.op=OR' \
          f'&q=*%3A*' \
          f'&terms.fl=title_abstract' \
          f'&terms.limit={limit}' \
          f'&terms.prefix={term_prefix}' \
          f'&terms.stats=true' \
          f'&terms.ttf=true' \
          f'&terms=true' \
          f'&useParams='

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        terms = response.json()['terms']['title_abstract']
        return [
            TermStats(term=terms[i],
                      df=terms[i + 1]['df'],
                      ttf=terms[i + 1]['ttf'])
            for i in range(0, len(terms), 2)
        ]


@router.get('/nql/grammar', response_model=str)
async def nql_grammar() -> str:
    return GRAMMAR


class QueryResult(BaseModel):
    n_docs: int
    docs: list[AcademicItemModel] | list[FullLexisNexisItemModel]


@router.get('/nql/query', response_model=QueryResult)
async def nql_query(query: str,
                    page: int = 1,
                    limit: int = 20,
                    permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> QueryResult:
    async with db_engine.session() as session:  # type: AsyncSession
        project_id = permissions.permissions.project_id
        project_type = (await session.scalar(select(Project.type).where(Project.project_id == project_id)))
        q = Query(query, project_id=project_id, project_type=project_type)

        stmt = q.stmt.subquery()
        cnt_stmt = func.count(stmt.c.item_id)

        if project_type == ItemType.academic:
            docs = [AcademicItemModel.model_validate(item.__dict__)
                    for item in (await session.execute(q.stmt
                                                       .offset((page - 1) * limit)
                                                       .limit(limit))).scalars().all()]
        elif project_type == ItemType.lexis:
            docs = lexis_orm_to_model((await session.execute(q.stmt
                                                             .offset((page - 1) * limit)
                                                             .limit(limit))).mappings().all())
        else:
            raise NotImplementedError()
        return QueryResult(
            n_docs=(await session.execute(cnt_stmt)).scalar(),  # type: ignore[arg-type]
            docs=docs
        )
