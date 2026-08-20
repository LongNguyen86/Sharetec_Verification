from fastapi import FastAPI
from src.bsdc_engine.api.routers import ingest, convert, validate, rules

app = FastAPI(
    title="BSDC Ingest Engine API",
    description="Refactored Enterprise Modular API without subprocess overhead",
    version="2.0.0",
)

app.include_router(ingest.router)
app.include_router(convert.router)
app.include_router(validate.router)
app.include_router(rules.router)

@app.get("/")
def root():
    return {"message": "BSDC Ingest Engine v2.0 is Running!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.bsdc_engine.api.app:app", host="127.0.0.1", port=8000, reload=True)