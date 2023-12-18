import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends
import sqlalchemy.sql.functions as func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nacsos_data.db.engine import ensure_session
from nacsos_data.db.schemas import Project, ItemType
from nacsos_data.util.nql import NQLQuery, NQLFilter
from nacsos_data.util.academic.openalex import query_async, SearchResult
from nacsos_data.models.items import AcademicItemModel, FullLexisNexisItemModel, GenericItemModel
from nacsos_data.models.openalex.solr import SearchField, DefType, OpType

from server.util.security import UserPermissionChecker, UserPermissions
from server.util.logging import get_logger
from server.util.config import settings
from server.data import db_engine

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


class QueryResult(BaseModel):
    n_docs: int
    docs: list[AcademicItemModel] | list[FullLexisNexisItemModel] | list[GenericItemModel]


@ensure_session
async def _get_query(session: AsyncSession, query: NQLFilter, project_id: str) -> NQLQuery:
    project_type: ItemType | None = (
        await session.scalar(select(Project.type).where(Project.project_id == project_id)))

    if project_type is None:
        raise KeyError(f'Found no matching project for {project_id}. This should NEVER happen!')

    return NQLQuery(query, project_id=str(project_id), project_type=project_type)


@router.post('/nql/query', response_model=QueryResult)
async def nql_query(query: NQLFilter,
                    page: int = 1,
                    limit: int = 20,
                    permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> QueryResult:
    async with db_engine.session() as session:  # type: AsyncSession
        nql = await _get_query(session=session, query=query, project_id=permissions.permissions.project_id)

        n_docs = (await session.execute(func.count(nql.stmt.subquery().c.item_id))).scalar()
        docs = await nql.results_async(session=session, limit=limit, offset=(page - 1) * limit)

        return QueryResult(n_docs=n_docs, docs=docs)  # type: ignore[arg-type]


@router.post('/nql/count', response_model=int)
async def nql_query_count(query: NQLFilter,
                          permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> int:
    async with db_engine.session() as session:  # type: AsyncSession
        nql = await _get_query(session=session, query=query, project_id=permissions.permissions.project_id)
        return (await session.execute(func.count(nql.stmt.subquery().c.item_id))).scalar()  # type: ignore[return-value]
