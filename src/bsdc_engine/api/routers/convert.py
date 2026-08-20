from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from src.bsdc_engine.io.excel_converter import ExcelConverter
from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Conversion"])


class ConvertRequest(BaseModel):
    run_id: str


@router.post("/convert-to-csv")
def convert_to_csv(payload: ConvertRequest):
    try:
        ws = RunWorkspace(run_id=payload.run_id)
        converter = ExcelConverter(output_dir=ws.csv_dir)
        csv_files = converter.convert_all_in_dir(input_dir=ws.raw_dir)

        return {
            "status": "success",
            "run_id": ws.run_id,
            "converted_csv_count": len(csv_files),
            "csv_files": [str(p) for p in csv_files],
        }
    except Exception as e:
        logger.error(f"CSV Conversion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))