from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.validate.mapping import MappingValidator
from src.bsdc_engine.logging import get_logger
import re
logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Validation"])


class ValidateRequest(BaseModel):
    run_id: str
    cu_id: str | None = None


@router.post("/validate-mapping")
def validate_mapping(payload: ValidateRequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)

        # Clean up any non-matching mapping files before validation
        # Clean up any non-matching mapping files before validation (Case-Insensitive)
        if payload.cu_id and ws.mapping_dir.exists():
            clean_cu = re.sub(r'[^a-z0-9]', '', payload.cu_id.lower())
            for f in list(ws.mapping_dir.glob("*.xlsx")):
                if "mapping" in f.name.lower():
                    clean_fname = re.sub(r'[^a-z0-9]', '', f.name.lower())
                    if clean_cu not in clean_fname:
                        logger.info(f"Filtering out non-matching mapping file: {f.name} for CU [{payload.cu_id}]")
                        f.unlink(missing_ok=True)

        validator = MappingValidator(raw_dir=ws.mapping_dir, output_report_dir=ws.qa_reports_dir)
        is_passed, errors = validator.validate()

        files_validated = [f.name for f in validator.mapping_files]

        return {
            "status": "success" if is_passed else "warning",
            "run_id": ws.run_id,
            "files_validated": files_validated,
            "is_valid": is_passed,
            "errors": errors,
            "qa_reports_dir": str(ws.qa_reports_dir),
        }
    except FileNotFoundError as e:
        return {"status": "warning", "message": str(e)}
    except Exception as e:
        logger.error(f"Validate Mapping failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))