import csv
import os
import tempfile
# import rispy
import xlsxwriter
import json
import uuid
import enum
import datetime as dt
from typing import TYPE_CHECKING, Callable, Any

# from memory_profiler import profile

from nacsos_data.db.crud.projects import read_project_by_id

from fastapi import APIRouter, Depends
from nacsos_data.models.nql import NQLFilter
from nacsos_data.util.annotations.export import (
    prepare_export_table,
    get_project_labels,
    get_project_scopes,
    get_project_bot_scopes,
    get_project_users,
    LabelOptions,
)
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from server.util.security import UserPermissionChecker

from nacsos_data.util.auth import UserPermissions

from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


def cleanup(file: str) -> None:
    os.remove(file)


class CFR(FileResponse):  # custom file response to set the media type
    media_type = 'application/csv'


class ExportRequest(BaseModel):
    labels: list[LabelOptions]
    nql_filter: NQLFilter | None = None
    bot_annotation_metadata_ids: list[str] | None = None
    assignment_scope_ids: list[str] | None = None
    user_ids: list[str] | None = None
    ignore_hierarchy: bool = True
    ignore_repeat: bool = True


@router.post('/annotations/csv', response_class=CFR)
async def get_annotations_csv(
    query: ExportRequest,
    max_results: int = 15000,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> FileResponse:
    result = await prepare_export_table(
        bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
        assignment_scope_ids=query.assignment_scope_ids,
        user_ids=query.user_ids,
        project_id=permissions.permissions.project_id,
        labels=query.labels,
        nql_filter=query.nql_filter,
        ignore_repeat=query.ignore_repeat,
        ignore_hierarchy=query.ignore_hierarchy,
        db_engine=db_engine,
        max_results=max_results,
    )

    with tempfile.NamedTemporaryFile(suffix='.csv', mode='w', newline='', delete=False) as fp:
        writer = csv.DictWriter(fp, fieldnames=list(result[0].keys()))
        writer.writeheader()
        [writer.writerow(lab) for lab in result]

        return FileResponse(fp.name, background=BackgroundTask(cleanup, fp.name), media_type='application/csv')


@router.post('/annotations/csv/v2', response_class=CFR)
async def get_annotations_csv_v2(
    query: ExportRequest,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> FileResponse:
    result = await prepare_export_table(
        bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
        assignment_scope_ids=query.assignment_scope_ids,
        user_ids=query.user_ids,
        project_id=permissions.permissions.project_id,
        labels=query.labels,
        nql_filter=query.nql_filter,
        ignore_repeat=query.ignore_repeat,
        ignore_hierarchy=query.ignore_hierarchy,
        db_engine=db_engine,
    )

    drop_cols: set[str] = {
        # 'item_id_1',
        'type',
        'time_edited',
        'project_id',
        # 'project_id_1',
        'title_slug',
        'keywords',
        # 'authors',
        'meta',
    }

    # todo: handle authors

    result = [{k: v for k, v in lab.items() if k not in drop_cols} for lab in result]
    headers = result[0].keys()

    # data = data.drop(
    #         columns=[
    #             'item_id_1',
    #             'type',
    #             'time_edited',
    #             'project_id',
    #             'project_id_1',
    #             'title_slug',
    #             'keywords',
    #             'authors',
    #             'meta',
    #         ],
    #     ).astype(
    #         {
    #             'publication_year': 'Int32',
    #             'incl|0': 'Int8',
    #             'incl|1': 'Int8',
    #             'reason|0': 'Int8',
    #             'reason|1': 'Int8',
    #             'reason|2': 'Int8',
    #         },
    #     )

    with tempfile.NamedTemporaryFile(suffix='.csv', mode='w', newline='', delete=False) as fp:
        writer = csv.DictWriter(fp, fieldnames=headers)
        writer.writeheader()
        [writer.writerow(lab) for lab in result]

        return FileResponse(fp.name, background=BackgroundTask(cleanup, fp.name), media_type='application/csv')


Converter = Callable[[Any], Any | None]


# measure time, can you used vectorized functions?
def build_converters(column_types: dict[str, type]) -> dict[str, Converter]:
    """
    Build conversion functions for each column type.

    Args:
        column_types: Dict mapping column names to their types
                     e.g., {'id': UUID, 'status': MyEnum, 'created': datetime}

    Returns:
        Dict mapping column names to conversion functions that handle None values
    """
    converters: dict[str, Converter] = {}

    for col, col_type in column_types.items():
        base_converter = _get_base_converter(col_type)
        # Wrap converter to handle None values
        converter = None
        if col_type is not None:
            converter = base_converter(col_type)
        converters[col] = converter

    return converters


def _get_base_converter(col_type: type[Any]) -> Callable[[Any], Any]:
    """Get the base conversion function for a type."""
    if col_type is uuid.UUID:
        return str
    elif issubclass(col_type, enum.Enum):
        return lambda v: v.value
    elif col_type in (dt.datetime, dt.time, dt.date):
        return lambda v: v.isoformat()
    elif col_type in (list, dict):
        return lambda v: json.dumps(v)
    else:
        return lambda v: v


def convert_types_inplace(data: list[dict[str, Any]], converters: dict[str, Converter]) -> None:
    """
    Convert types in place, filtering converters to only present columns.

    Args:
        data: List of dictionaries from database query
        converters: Pre-built converters dict from build_converters()
    Time Complexity: O(n × m) where n = rows, m = columns needing conversion
    Space Complexity: O(m) for filtered converters dict
    """
    if not data:
        return

    # Filter converters to only columns present in this request (one-time check)
    present_converters = {col: converter for col, converter in converters.items() if col in data[0]}

    # Apply converters to each row
    for row in data:
        for col, converter in present_converters.items():
            row[col] = converter(row[col])


def present_count(d: dict[str, Any]) -> int:
    # counts keys whose value is not None (adjust if you consider "" empty too)
    return sum(v is not None for v in d.values())


# @profile
@router.post('/annotations/excel', response_class=CFR)
async def get_annotations_excel(
    query: ExportRequest,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> FileResponse:
    result = await prepare_export_table(
        # this function should do the removal of duplicate columns? with/out pandas?
        # also should i avoid pandas here or use it if it's being used elsewhere..?
        bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
        assignment_scope_ids=query.assignment_scope_ids,
        user_ids=query.user_ids,
        project_id=permissions.permissions.project_id,
        labels=query.labels,
        nql_filter=query.nql_filter,
        ignore_repeat=query.ignore_repeat,
        ignore_hierarchy=query.ignore_hierarchy,
        db_engine=db_engine,
    )

    best_row = max(result, key=present_count)
    column_types: dict[str, type[Any]] = {key: type(value) for (key, value) in best_row.items()}
    if column_types.get('time_edited'):
        column_types['time_edited'] = dt.datetime
    converters = build_converters(column_types)
    convert_types_inplace(result, converters)

    # Create a temporary file
    with tempfile.NamedTemporaryFile(suffix='.xlsx', mode='wb', delete=False) as fp:
        temp_path = fp.name

    # Create a workbook and worksheet
    wb = xlsxwriter.Workbook(temp_path)
    ws = wb.add_worksheet('NACSOS Annotations Export')

    # Write header row
    headers = list(result[0].keys())

    ws.write_row(0, 0, headers)

    # Write data rows
    for row_idx, row in enumerate(result, start=1):
        try:
            ws.write_row(row_idx, 0, [row.get(h) for h in headers])
        except Exception as e:
            print(row)
            raise Exception(e)

    # Set column widths
    for col_idx, header in enumerate(headers):
        ws.set_column(col_idx, col_idx, max(20, len(str(header)) + 2))

    # Freeze header row
    ws.freeze_panes(1, 0)
    # Close the workbook to flush all data
    wb.close()

    # Return the file
    return FileResponse(
        temp_path, background=BackgroundTask(cleanup, temp_path), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# @router.post('/annotations/ris', response_class=CFR)
# async def get_annotations_ris(
#     query: ExportRequest,
#     permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
# ) -> FileResponse:
#     labels = query.labels
#     result = await prepare_export_table(
#         bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
#         assignment_scope_ids=query.assignment_scope_ids,
#         user_ids=query.user_ids,
#         project_id=permissions.permissions.project_id,
#         labels=labels,
#         nql_filter=query.nql_filter,
#         ignore_repeat=query.ignore_repeat,
#         ignore_hierarchy=query.ignore_hierarchy,
#         db_engine=db_engine,
#     )

#     with tempfile.NamedTemporaryFile(suffix='.ris', mode='w', delete=False) as bibliography_file:
#         rispy.dump(
#             [
#                 {  # In prod academic_items; up to 2.5M out of 8M missing for these columns; so to make it error prone, default to empty string
#                     'abstract': row.get('text'),
#                     'title': row.get('title'),
#                     'doi': f'https://doi.org/{row.get("doi")}',
#                     'custom1': row.get('openalex_id'),
#                     'custom2': str(row.get('item_id')),
#                     'year': row.get('publication_year'),
#                     'journal_name': row.get('source'),
#                     'authors': [author.get('name') for author in row.get('authors')] if row.get('authors') else [],
#                     'keywords': (row.get('keywords') if row.get('keywords') else [])
#                     + [f'{l.key}|{li}' for l in labels if l.options_int is not None for li in l.options_int if row[f'{l.key}|{li}'] == 1]
#                     + [f'{l.key}|{li}' for l in labels if l.options_multi is not None for li in l.options_multi if row[f'{l.key}|{li}'] == 1]
#                     + [f'{l.key}|{li}' for l in labels if l.options_bool is not None for li in [0, 1] if row[f'{l.key}|{li}'] == 1],
#                     'label': [f'{l.key}|{li}' for l in labels if l.options_int is not None for li in l.options_int if row[f'{l.key}|{li}'] == 1]
#                     + [f'{l.key}|{li}' for l in labels if l.options_multi is not None for li in l.options_multi if row[f'{l.key}|{li}'] == 1]
#                     + [f'{l.key}|{li}' for l in labels if l.options_bool is not None for li in [0, 1] if row[f'{l.key}|{li}'] == 1],
#                     'notes': [
#                         f'openalex: {row.get("openalex_id")}\n'
#                         f'nacsos: {row.get("item_id")}\n'
#                         f'annotated by: {row.get("username")}\n'
#                         f'Annotations: '
#                         + ', '.join(
#                             [f'{l.key}|{li}' for l in labels if l.options_int is not None for li in l.options_int if row[f'{l.key}|{li}'] == 1]
#                             + [f'{l.key}|{li}' for l in labels if l.options_multi is not None for li in l.options_multi if row[f'{l.key}|{li}'] == 1]
#                             + [f'{l.key}|{li}' for l in labels if l.options_bool is not None for li in [0, 1] if row[f'{l.key}|{li}'] == 1]
#                         )
#                     ],
#                 }
#                 for row in result
#             ],
#             bibliography_file,
#         )

#         return FileResponse(
#             bibliography_file.name, background=BackgroundTask(cleanup, bibliography_file.name), media_type='application/x-research-info-systems'
#         )


class ProjectBaseInfoEntry(BaseModel):
    id: str | uuid.UUID
    name: str


class ProjectBaseInfoScopeEntry(ProjectBaseInfoEntry):
    scheme_id: str | uuid.UUID
    scheme_name: str


class ProjectBaseInfo(BaseModel):
    users: list[ProjectBaseInfoEntry]
    scopes: list[ProjectBaseInfoScopeEntry]
    bot_scopes: list[ProjectBaseInfoEntry]
    labels: dict[str, LabelOptions]


@router.get('/project/baseinfo', response_model=ProjectBaseInfo)
async def get_export_baseinfo(
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> ProjectBaseInfo:
    project_users = await get_project_users(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_scopes = await get_project_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_bot_scopes = await get_project_bot_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_labels = await get_project_labels(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project = await read_project_by_id(project_id=permissions.permissions.project_id, engine=db_engine)

    if project is None:
        raise RuntimeError('Invalid state!')

    return ProjectBaseInfo(
        users=[ProjectBaseInfoEntry.model_validate(pu) for pu in project_users],
        scopes=[ProjectBaseInfoScopeEntry.model_validate(ps) for ps in project_scopes],
        bot_scopes=[ProjectBaseInfoEntry.model_validate(pbs) for pbs in project_bot_scopes],
        labels=project_labels,
    )
