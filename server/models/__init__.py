import uuid

from nacsos_data.models.imports import M2MImportItemType
from pydantic import BaseModel


class ImportM2M(BaseModel):
    import_id: str | uuid.UUID
    item_id: str | uuid.UUID
    type: M2MImportItemType
    first_revision: int
    latest_revision: int
