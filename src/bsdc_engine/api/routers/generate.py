from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.generate.builders import TransformationBuilder
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/generate", tags=["Data Generation"])


class GenerateDataRequest(BaseModel):
    run_id: str
    cu_id: str | None = None
    sheet_name: str | None = None


@router.post("/data")
def generate_transformed_data(payload: GenerateDataRequest):
    """
    Trigger the Data Transformation Engine to generate expected CSV tables based on rules in SQLite DB.
    """
    try:
        ws = RunWorkspace(run_id=payload.run_id)

        # Initialize the transformation builder using the isolated workspace paths
        builder = TransformationBuilder(
            raw_data_dir=ws.csv_dir,
            output_dir=getattr(ws, "transformed_dir", ws.reconciliation_dir),
            db_path=getattr(ws, "db_path", None),
        )

        # Execute data transformation across all mapped sections
        results = builder.generate_all(cu_id=payload.cu_id)

        logger.info(
            f"Successfully generated {len(results)} transformed CSV files for run_id: {payload.run_id}"
        )

        return {
            "status": "success",
            "run_id": payload.run_id,
            "cu_id": payload.cu_id,
            "generated_count": len(results),
            "files": [
                {
                    "sheet_name": getattr(r, "sheet_name", ""),
                    "section_name": getattr(r, "section_name", ""),
                    "rows": getattr(r, "rows_generated", 0),
                    "output_file": str(getattr(r, "output_file", r)),
                }
                for r in results
            ],
        }

    except Exception as e:
        logger.error(f"Data generation process failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))