import sys
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, HTTPException
import polars as pl
from pydantic import BaseModel

from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.verify.key_matcher import KeyMatcher, detect_key_columns
from src.bsdc_engine.verify.comparator import DataComparator
from src.bsdc_engine.verify.formats import FormatValidator
from src.bsdc_engine.verify.aggregates import AggregateChecker
from src.bsdc_engine.verify.reporter import VerificationReporter
from src.bsdc_engine.workspace import RunWorkspace

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/verify", tags=["Perform Verification"])


class VerificationRequest(BaseModel):
    run_id: str
    cu_id: str
    section_name: Optional[str] = None
    key_cols: Optional[List[str]] = None
    compare_cols: Optional[List[str]] = None
    numeric_cols: Optional[List[str]] = []


@router.post("/run")
def run_perform_verification(payload: VerificationRequest):
    """
    FastAPI endpoint called by n8n 'Perform Verification' node.
    Reads Sharetec actual CSV files from centralized workspace/Actual_Sharetec directory.
    """
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        run_root = ws.raw_dir.parent.parent

        exp_dir = getattr(ws, "reconciliation_dir", None) or (run_root / "out" / "reconciliation")
        
        # Centralized Sharetec Actual Directory in workspace
        act_dir = Path("workspace/Actual_Sharetec")
        act_dir.mkdir(parents=True, exist_ok=True)

        if payload.section_name:
            target_files = [exp_dir / f"{payload.section_name}.csv"]
        else:
            target_files = list(exp_dir.glob("Expected_*.csv"))

        if not target_files:
            raise HTTPException(
                status_code=404,
                detail=f"No Expected CSV files found in reconciliation directory: {exp_dir}"
            )

        all_section_results = []
        overall_total_discrepancies = 0

        for exp_file in target_files:
            sec_stem = exp_file.stem
            act_file = act_dir / f"{sec_stem}.csv"

            if not act_file.exists():
                logger.warning(f"Skipping [{sec_stem}]: Missing matching Actual file in {act_dir}")
                continue

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            key_columns = payload.key_cols if payload.key_cols else detect_key_columns(df_exp.columns, sec_stem)
            compare_columns = payload.compare_cols if payload.compare_cols else df_exp.columns

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)
            agg_issues = AggregateChecker.check_control_totals(exp_map, act_map, payload.numeric_cols or [])

            section_discrepancies = len(mismatches) + len(fmt_issues) + len(agg_issues)
            overall_total_discrepancies += section_discrepancies

            all_section_results.append({
                "section_name": sec_stem,
                "expected_rows": len(exp_map),
                "actual_rows": len(act_map),
                "mismatches": mismatches,
                "fmt_issues": fmt_issues,
                "agg_issues": agg_issues,
                "status": "PASSED" if section_discrepancies == 0 else "FAILED"
            })

        output_dir = Path("test-output")
        VerificationReporter.generate_combined_report(all_section_results, output_dir=output_dir)

        overall_status = "PASSED" if overall_total_discrepancies == 0 else "FAILED"

        return {
            "status": overall_status,
            "run_id": payload.run_id,
            "cu_id": payload.cu_id,
            "total_sections_verified": len(all_section_results),
            "total_discrepancies": overall_total_discrepancies,
            "report_html_path": str(output_dir / "ExtentDataReport.html"),
            "sections": [
                {
                    "section_name": r["section_name"],
                    "expected_rows": r["expected_rows"],
                    "actual_rows": r["actual_rows"],
                    "discrepancies_count": len(r["mismatches"]) + len(r["fmt_issues"]) + len(r["agg_issues"]),
                    "status": r["status"]
                }
                for r in all_section_results
            ]
        }

    except Exception as e:
        logger.error(f"Perform Verification API failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))