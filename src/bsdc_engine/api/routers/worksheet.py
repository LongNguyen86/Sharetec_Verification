import sys
import re
import shutil
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
        exp_dir = getattr(ws, "data_process_dir", None) or getattr(ws, "reconciliation_dir", None) or (ws.out_dir / "data_process")
        act_dir = ws.actual_dir
        
        target_files = list(exp_dir.glob("*.csv"))
        if not target_files:
            raise HTTPException(
                status_code=404,
                detail=f"No Expected CSV files found for worksheet generation in: {exp_dir}"
            )

        # 1. Index actual_sharetec files by clean base name (supports _extracted_data, _processed_data, or raw stem)
        actual_files_map = {}
        for f in act_dir.glob("*.csv"):
            base = re.sub(r"_(extracted|processed)_data$", "", f.stem, flags=re.IGNORECASE).strip().lower()
            actual_files_map[base] = f
            actual_files_map[f.stem.lower()] = f

        verification_results = []
        temp_alias_files = []

        for exp_file in target_files:
            # Strip suffix to restore the exact clean base name used prior to renaming (e.g., EVIZITEST_Shares)
            base_name = re.sub(r"_(processed|extracted)_data$", "", exp_file.stem, flags=re.IGNORECASE).strip()

            # Locate matching actual file
            act_file = actual_files_map.get(base_name.lower()) or actual_files_map.get(exp_file.stem.lower())
            if not act_file or not act_file.exists():
                logger.warning(f"Skipping [{base_name}]: Missing matching Actual file in {act_dir}")
                continue

            # Ensure act_dir contains base_name.csv so assembler can sample rows seamlessly
            canonical_act_file = act_dir / f"{base_name}.csv"
            if not canonical_act_file.exists():
                shutil.copyfile(act_file, canonical_act_file)
                temp_alias_files.append(canonical_act_file)

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            # Pass base_name (e.g. EVIZITEST_Shares) for correct key column detection
            key_columns = detect_key_columns(df_exp.columns, base_name)
            compare_columns = df_exp.columns

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)

            # Pass clean base_name so the internal assembler maps "Shares" -> "Deposit Reg & SD" exactly as before
            verification_results.append({
                "section_name": base_name,
                "mismatches": mismatches,
                "fmt_issues": fmt_issues,
            })

        # 2. Execute Task 32 & Task 33 assembly logic
        try:
            worksheet_html_path = assemble_verification_worksheet(
                ws=ws,
                cu_id=payload.cu_id,
                verification_results=verification_results,
                act_dir=act_dir,
                sp_template_relative_path=payload.sharepoint_template_path
            )
        finally:
            # Clean up temporary alias files
            for temp_f in temp_alias_files:
                try:
                    if temp_f.exists():
                        temp_f.unlink()
                except Exception:
                    pass

        dest_path = ws.test_output_dir / Path(worksheet_html_path).name
        if Path(worksheet_html_path).resolve() != dest_path.resolve():
            shutil.move(str(worksheet_html_path), str(dest_path))
            worksheet_html_path = dest_path

        return {
            "status": "success",
            "run_id": payload.run_id,
            "cu_id": payload.cu_id,
            "worksheet_path": str(worksheet_html_path)
        }

    except Exception as e:
        logger.error(f"Worksheet assembly endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))