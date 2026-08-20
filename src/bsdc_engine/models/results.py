from typing import Any, Optional
from pydantic import BaseModel


class GenerateResult(BaseModel):
    cu_id: str
    sheet_name: str
    section_name: str
    rows_generated: int
    output_file: str


class VerifyResult(BaseModel):
    cu_id: str
    report_path: str
    total_rules: int
    approved_count: int
    edited_count: int
    rejected_count: int


class RunSummary(BaseModel):
    run_id: str
    cu_id: str
    status: str
    downloaded_files: list[str] = []
    converted_csvs: list[str] = []
    generated_results: list[GenerateResult] = []
    errors: list[str] = []