from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
import polars as pl
from pydantic import BaseModel

from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.verify.key_matcher import KeyMatcher, detect_key_columns
from src.bsdc_engine.verify.comparator import DataComparator
from src.bsdc_engine.verify.formats import FormatValidator
from src.bsdc_engine.verify.worksheet_assembler import assemble_verification_worksheet
from src.bsdc_engine.workspace import RunWorkspace

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/worksheet", tags=["Verification Worksheet"])


class WorksheetRequest(BaseModel):
    run_id: str
    cu_id: str
    sharepoint_template_path: Optional[str] = None


@router.post("/assemble")
def assemble_worksheet_endpoint(payload: WorksheetRequest):
    """
    FastAPI endpoint dedicated to populating sample records (Failed-first) 
    and updating status dropdowns in the HTML Verification Worksheet.
    """
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        exp_dir = getattr(ws, "reconciliation_dir", None) or (ws.out_dir / "reconciliation")
        
        # Use workspace actual_dir (workspace/runs//in/actual_sharetec)
        act_dir = ws.actual_dir
        target_files = list(exp_dir.glob("*.csv"))

        if not target_files:
            raise HTTPException(
                status_code=404,
                detail=f"No Expected CSV files found for worksheet generation in: {exp_dir}"
            )

        verification_results = []
        for exp_file in target_files:
            sec_stem = exp_file.stem
            act_file = act_dir / f"{sec_stem}.csv"

            if not act_file.exists():
                continue

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            key_columns = detect_key_columns(df_exp.columns, sec_stem)
            compare_columns = df_exp.columns

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)

            verification_results.append({
                "section_name": sec_stem,
                "mismatches": mismatches,
                "fmt_issues": fmt_issues,
            })

        # Execute Task 32 & Task 33 assembly logic
        worksheet_html_path = assemble_verification_worksheet(
            ws=ws,
            cu_id=payload.cu_id,
            verification_results=verification_results,
            act_dir=act_dir,
            sp_template_relative_path=payload.sharepoint_template_path
        )

        return {
            "status": "success",
            "run_id": payload.run_id,
            "cu_id": payload.cu_id,
            "worksheet_path": str(worksheet_html_path)
        }

    except Exception as e:
        logger.error(f"Worksheet assembly endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))