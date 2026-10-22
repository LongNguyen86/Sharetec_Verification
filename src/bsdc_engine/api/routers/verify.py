import sys
import re
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, HTTPException
import polars as pl
from pydantic import BaseModel

from src.bsdc_engine.config import settings
from src.bsdc_engine.io.sharepoint import SharePointClient
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
    Compares processed reconciliation data against actual Sharetec extracted files.
    """
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        run_root = ws.raw_dir.parent.parent

        exp_dir = getattr(ws, "data_process_dir", None) or getattr(ws, "reconciliation_dir", None) or (run_root / "output" / "data_process")
        act_dir = getattr(ws, "actual_dir", None) or (ws.raw_dir.parent / "actual_sharetec")
        act_dir.mkdir(parents=True, exist_ok=True)

        if not exp_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No Expected CSV files found in data_process directory: {exp_dir}"
            )

        # 1. Filter target expected files in reconciliation directory
        if payload.section_name:
            sec_clean = re.sub(r"_processed_data$", "", payload.section_name, flags=re.IGNORECASE).strip()
            target_files = [
                f for f in exp_dir.glob("*.csv")
                if re.sub(r"_processed_data$", "", f.stem, flags=re.IGNORECASE).strip().lower() == sec_clean.lower()
                or f.stem.lower() == payload.section_name.lower()
            ]
            if not target_files:
                target_files = [exp_dir / f"{payload.section_name}.csv"]
        else:
            target_files = list(exp_dir.glob("*.csv"))

        if not target_files:
            raise HTTPException(
                status_code=404,
                detail=f"No Expected CSV files found in reconciliation directory: {exp_dir}"
            )

        # 2. Build index map of actual_sharetec files
        actual_files_map = {}
        for f in act_dir.glob("*.csv"):
            actual_stem = f.stem
            base_actual = re.sub(r"_extracted_data$", "", actual_stem, flags=re.IGNORECASE).strip().lower()
            actual_files_map[base_actual] = f
            actual_files_map[actual_stem.lower()] = f

        all_section_results = []
        overall_total_discrepancies = 0

        # 3. Match and reconcile each pair
        for exp_file in target_files:
            exp_stem = exp_file.stem
            base_name = re.sub(r"_processed_data$", "", exp_stem, flags=re.IGNORECASE).strip()

            act_file = actual_files_map.get(base_name.lower()) or actual_files_map.get(exp_stem.lower())
            if not act_file or not act_file.exists():
                logger.warning(
                    f"Skipping [{base_name}]: Missing matching Actual file (*_extracted_data.csv) in {act_dir}"
                )
                continue

            logger.info(f"Reconciling: [{exp_file.name}] <---> [{act_file.name}]")

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            # Record physical row count directly from both DataFrames
            exp_total_rows = df_exp.height
            act_total_rows = df_act.height

            # Detect key columns
            key_columns = payload.key_cols if payload.key_cols else detect_key_columns(df_exp.columns, base_name)
            compare_columns = payload.compare_cols if payload.compare_cols else df_exp.columns

            # Check if any key column has valid non-empty data without passing Polars Expr to Series
            key_has_values = False
            for k in key_columns:
                if k in df_exp.columns:
                    raw_vals = [
                        str(v).strip() for v in df_exp[k].drop_nulls().to_list() if str(v).strip()
                    ]
                    if any(v.lower() not in ("", "null", "none") for v in raw_vals):
                        key_has_values = True
                        break

            # Fallback to physical row index matching if key columns are completely unparsed/blank
            if not key_has_values and exp_total_rows > 0:
                logger.warning(
                    f"Key columns {key_columns} have no data in [{base_name}]. Falling back to row indices for comparison."
                )
                row_key = "__row_idx_key__"
                df_exp = df_exp.with_columns(
                    pl.Series(row_key, [str(i) for i in range(exp_total_rows)])
                )
                df_act = df_act.with_columns(
                    pl.Series(row_key, [str(i) for i in range(act_total_rows)])
                )
                key_columns = [row_key]

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)
            agg_issues = AggregateChecker.check_control_totals(exp_map, act_map, payload.numeric_cols or [])

            section_discrepancies = len(mismatches) + len(fmt_issues) + len(agg_issues)
            overall_total_discrepancies += section_discrepancies

            all_section_results.append({
                "section_name": base_name,
                "expected_file": exp_file.name,
                "actual_file": act_file.name,
                "expected_rows": exp_total_rows,
                "actual_rows": act_total_rows,
                "mismatches": mismatches,
                "fmt_issues": fmt_issues,
                "agg_issues": agg_issues,
                "status": "PASSED" if section_discrepancies == 0 else "FAILED"
            })

        output_dir = ws.test_output_dir
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
                    "expected_file": r["expected_file"],
                    "actual_file": r["actual_file"],
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