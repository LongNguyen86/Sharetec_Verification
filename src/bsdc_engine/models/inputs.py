from typing import Optional
from pydantic import BaseModel, Field


class FetchInputRequest(BaseModel):
    cu_id: str = Field(default="MEDICOOP", description="Credit Union Identifier")
    mapping_path: Optional[str] = None
    raw_data_path: Optional[str] = None
    matrix_path: Optional[str] = None


class TableSpec(BaseModel):
    table_name: str
    column_count: int
    row_count: int
    file_path: str


class InputInventory(BaseModel):
    cu_id: str
    mapping_files: list[str] = []
    raw_csv_files: list[str] = []
    matrix_files: list[str] = []