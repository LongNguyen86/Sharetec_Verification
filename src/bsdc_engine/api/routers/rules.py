from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.rules.parser import parse_all_mapping_sheets
from src.bsdc_engine.rulegen.drafter import RuleDrafter
from src.bsdc_engine.report.rule_verification import export_rule_verification_report
from src.bsdc_engine.rules.decisions import apply_qa_decisions
from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.io.sharepoint import SharePointClient

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/rules", tags=["Rule Engine"])


class ParseRulesRequest(BaseModel):
    run_id: str
    cu_id: str


class AIParseRequest(BaseModel):
    run_id: str
    cu_id: str | None = None


class ExportReportRequest(BaseModel):
    run_id: str
    cu_id: str


class ApplyQARequest(BaseModel):
    run_id: str
    report_filename: str
    sharepoint_source_path: str | None = None

class ExportReportRequest(BaseModel):
    run_id: str
    cu_id: str
    sharepoint_target_path: str | None = None

@router.post("/parse")
def parse_rules(payload: ParseRulesRequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        parse_all_mapping_sheets(raw_dir=ws.mapping_dir, cu_id=payload.cu_id)
        return {
            "status": "success",
            "run_id": ws.run_id,
            "cu_id": payload.cu_id,
            "message": "Parsed mapping rules to rules.db",
        }
    except Exception as e:
        logger.error(f"Parse rules failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai-parse")
def ai_parse(payload: AIParseRequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        drafter = RuleDrafter()
        count = drafter.draft_pending_rules()
        return {
            "status": "success",
            "run_id": ws.run_id,
            "processed_rules_count": count,
            "message": f"AI Drafting completed. Processed {count} rules via Gemini.",
        }
    except Exception as e:
        logger.error(f"AI parse failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export-verification-report")
def export_verification_report(payload: ExportReportRequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        report_path = export_rule_verification_report(
            cu_id=payload.cu_id, output_dir=ws.qa_reports_dir
        )

        uploaded = False
        if payload.sharepoint_target_path:
            client = SharePointClient()
            uploaded = client.upload_file(
                local_file_path=report_path,
                target_folder_path=payload.sharepoint_target_path,
            )

        return {
            "status": "success",
            "run_id": ws.run_id,
            "cu_id": payload.cu_id,
            "report_path": str(report_path),
            "uploaded_to_sharepoint": uploaded,
        }
    except Exception as e:
        logger.error(f"Export verification report failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply-qa-decisions")
def apply_qa(payload: ApplyQARequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        report_file = ws.qa_reports_dir / payload.report_filename

        # Re-download the updated report from SharePoint if path provided
        if payload.sharepoint_source_path:
            client = SharePointClient()
            sp_folder = payload.sharepoint_source_path.strip()
            if not sp_folder.startswith("/"):
                sp_folder = "/" + sp_folder

            sp_file_path = f"{sp_folder.rstrip('/')}/{payload.report_filename}"
            logger.info(
                f"Downloading reviewed report from SharePoint: {sp_file_path}"
            )
            client.download_file_by_path(sp_file_path, ws.qa_reports_dir)

        stats = apply_qa_decisions(reviewed_report_path=report_file)
        return {"status": "success", "run_id": ws.run_id, "stats": stats}
    except Exception as e:
        logger.error(f"Apply QA decisions failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))