from app.api.document import router as document_router
from app.database.database import Base, engine
from fastapi import FastAPI

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Intelligent Document Processing Agent")

app.include_router(document_router, prefix="/api")


@app.get("/")
def home():
    return {"message": "Document Processing API is running"}
