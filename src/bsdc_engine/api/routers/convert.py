from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.io.excel_converter import ExcelConverter

router = APIRouter(prefix="/api/v1", tags=["Convert"])


class ConvertRequest(BaseModel):
    run_id: str
    cu_id: str | None = None


@router.post("/convert-to-csv")
def convert_to_csv_endpoint(req: ConvertRequest):
    try:
        ws = RunWorkspace(run_id=req.run_id)
        converter = ExcelConverter(output_dir=ws.csv_dir)
        csv_files = []

        # Scan and convert Excel files from both in/raw and work/matrix
        for target_dir in [ws.raw_dir, ws.matrix_dir]:
            if target_dir.exists():
                converted = converter.convert_all_in_dir(input_dir=target_dir)
                csv_files.extend(converted)

        return {
            "status": "success",
            "run_id": ws.run_id,
            "converted_csv_count": len(csv_files),
            "csv_files": [str(p) for p in csv_files],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))