import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Body
from sqlalchemy import text
import sqlalchemy.sql.functions as func

from nacsos_data.util.nql import NQLQuery, NQLFilter
from nacsos_data.util.academic.apis.openalex import SearchResult, OpenAlexSolrAPI
from nacsos_data.models.items import AcademicItemModel, FullLexisNexisItemModel, GenericItemModel
from nacsos_data.models.openalex import SearchField, DefType, OpType
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

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


class SearchPayload(BaseModel):
    query: str
    limit: int = 20
    offset: int = 0
    def_type: DefType = 'lucene'
    field: SearchField = 'title_abstract'
    histogram: bool = False
    op: OpType = 'AND'
    histogram_from: int = 1990
    histogram_to: int = 2024


@router.post('/openalex/select', response_model=SearchResult)
async def search_openalex(search: SearchPayload, permissions: UserPermissions = Depends(UserPermissionChecker('search_oa'))) -> SearchResult:
    return OpenAlexSolrAPI(
        openalex_conf=settings.OPENALEX,
        include_histogram=search.histogram,
        histogram_to=search.histogram_to,
        histogram_from=search.histogram_from,
        op=search.op,
        def_type=search.def_type,
        field=search.field,
    ).query(
        query=search.query,
        offset=search.offset,
        limit=search.limit,
    )


@router.get('/openalex/terms', response_model=list[TermStats])
async def term_expansion(term_prefix: str, limit: int = 20, permissions: UserPermissions = Depends(UserPermissionChecker('search_oa'))) -> list[TermStats]:
    url = (
        f'{settings.OA_SOLR}/terms'
        f'?facet=true'
        f'&indent=true'
        f'&q.op=OR'
        f'&q=*%3A*'
        f'&terms.fl=title_abstract'
        f'&terms.limit={limit}'
        f'&terms.prefix={term_prefix}'
        f'&terms.stats=true'
        f'&terms.ttf=true'
        f'&terms=true'
        f'&useParams='
    )

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        terms = response.json()['terms']['title_abstract']
        return [TermStats(term=terms[i], df=terms[i + 1]['df'], ttf=terms[i + 1]['ttf']) for i in range(0, len(terms), 2)]


class QueryResult(BaseModel):
    n_docs: int
    docs: list[AcademicItemModel] | list[FullLexisNexisItemModel] | list[GenericItemModel]


@router.post('/nql/query', response_model=QueryResult)
async def nql_query(
    query: NQLFilter, page: int = 1, limit: int = 20, permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))
) -> QueryResult:
    async with db_engine.session() as session:  # type: AsyncSession
        nql = await NQLQuery.get_query(session=session, query=query, project_id=str(permissions.permissions.project_id))

        n_docs = (await session.execute(func.count(nql.stmt.subquery().c.item_id))).scalar()
        docs = await nql.results_async(session=session, limit=limit, offset=(page - 1) * limit)

        return QueryResult(n_docs=n_docs, docs=docs)  # type: ignore[arg-type]


@router.post('/nql/count', response_model=int)
async def nql_query_count(query: NQLFilter | None = Body(default=None), permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> int:
    async with db_engine.session() as session:  # type: AsyncSession
        if not query:
            return await session.scalar(  # type: ignore[no-any-return]
                text('SELECT count(item_id) FROM item WHERE project_id = :project_id;'), {'project_id': permissions.permissions.project_id}
            )

        nql = await NQLQuery.get_query(session=session, query=query, project_id=str(permissions.permissions.project_id))
        return (await session.execute(func.count(nql.stmt.subquery().c.item_id))).scalar()  # type: ignore[return-value]
