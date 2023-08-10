import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends
import sqlalchemy.sql.functions as func
from typing import TYPE_CHECKING

from nacsos_data.util.academic.openalex import query_async, SearchResult, SearchField, DefType, OpType
from nacsos_data.db.crud.items import Query
from nacsos_data.models.items import AcademicItemModel

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


@router.get('/openalex/select', response_model=SearchResult)
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
                             openalex_endpoint=f'{settings.OA_SOLR}/select',
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


class QueryResult(BaseModel):
    n_docs: int
    docs: list[AcademicItemModel]


@router.get('/nql/query', response_model=QueryResult)
async def nql_query(query: str,
                    limit: int = 20,
                    permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> QueryResult:
    q = Query(query, project_id=permissions.permissions.project_id)

    async with db_engine.session() as session:  # type: AsyncSession
        stmt = q.stmt.subquery()
        cnt_stmt = func.count(stmt.c.item_id)
        return QueryResult(
            n_docs=(await session.execute(cnt_stmt)).scalar(),  # type: ignore[arg-type]
            docs=[AcademicItemModel.model_validate(item.__dict__)
                  for item in (await session.execute(q.stmt.limit(limit))).scalars().all()]
        )
