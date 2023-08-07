import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends

from nacsos_data.util.academic.openalex import query_async, SearchResult, SearchField, DefType, OpType

from server.util.security import UserPermissionChecker, UserPermissions
from server.util.logging import get_logger
from server.util.config import settings

logger = get_logger('nacsos.api.route.search')
router = APIRouter()

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
