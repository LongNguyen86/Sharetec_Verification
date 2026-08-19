import sys
import subprocess
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Initialize FastAPI application
app = FastAPI(
    title="BSDC Ingest Engine API",
    description="Local API service to handle file fetching, CSV conversion, and mapping validation",
    version="1.0.0",
)

BASE_DIR = Path(__file__).resolve().parent


class FetchInputRequest(BaseModel):
    cu_id: str = "MEDICOOP"
    mapping_path: Optional[str] = None
    raw_data_path: Optional[str] = None
    matrix_path: Optional[str] = None


class WorkflowRequest(BaseModel):
    cu_id: str = "MEDICOOP"


@app.get("/")
def root():
    return {"message": "Welcome to BSDC Ingest Engine API! Visit /docs for Swagger UI."}


@app.get("/health")
def health_check():
    """Health check endpoint to ensure API service is running"""
    return {"status": "ok", "message": "FastAPI is running locally"}


@app.post("/api/v1/fetch-input-files")
def fetch_input_files(payload: FetchInputRequest):
    """Endpoint 1: Replaces 'Fetch Input Files' node (runs main_ingest.py with --path arguments)"""
    try:
        script_path = BASE_DIR / "main_ingest.py"
        cmd = [sys.executable, str(script_path)]

        paths_to_fetch = [
            p for p in [payload.mapping_path, payload.raw_data_path, payload.matrix_path] if p
        ]

        if paths_to_fetch:
            cmd.append("--path")
            cmd.extend(paths_to_fetch)

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, encoding="utf-8"
        )
        output_text = (result.stdout or "").strip()

        # Handle business logic errors: Check for session or missing file issues
        if any(err_keyword in output_text for err_keyword in ["SHAREPOINT API ERROR", "NO FILES FOUND", "0 files"]):
            raise HTTPException(
                status_code=500,
                detail=f"SharePoint Download Failed! Check Session/Permissions.\nLogs:\n{output_text}"
            )

        return {
            "status": "success",
            "step": "fetch_input_files",
            "output": output_text,
        }
    except subprocess.CalledProcessError as e:
        error_msg = (e.stderr or e.stdout or str(e)).strip()
        raise HTTPException(
            status_code=500, detail=f"Fetch Input Files failed: {error_msg}"
        )


@app.post("/api/v1/convert-to-csv")
def convert_to_csv(payload: WorkflowRequest):
    """Endpoint 2: Replaces 'Convert to CSV' node (runs main_convert.py)"""
    try:
        script_path = BASE_DIR / "main_convert.py"
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        return {
            "status": "success",
            "step": "convert_to_csv",
            "output": (result.stdout or "").strip(),
        }
    except subprocess.CalledProcessError as e:
        error_msg = (e.stderr or e.stdout or str(e)).strip()
        raise HTTPException(
            status_code=500, detail=f"Convert to CSV failed: {error_msg}"
        )


@app.post("/api/v1/validate-mapping")
def validate_mapping(payload: WorkflowRequest):
    """Endpoint 3: Replaces 'Validate the mapping file' node (runs main_validate_mapping.py)"""
    try:
        script_path = BASE_DIR / "main_validate_mapping.py"
        # Set check=False to prevent raising 500 exception when mapping validation errors occur
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8"
        )
        output_text = (result.stdout or "").strip()

        # Mark status as warning if validation fails, allowing the workflow to proceed safely
        is_failed = result.returncode != 0 or "PREFLIGHT FAILED" in output_text

        return {
            "status": "warning" if is_failed else "success",
            "step": "validate_mapping",
            "output": output_text,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Validate Mapping execution failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)