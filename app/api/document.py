import hashlib
import logging
import uuid
from pathlib import Path

import filetype
import fitz
from app.database.database import get_db
from app.models.documents import Documents
from app.schemas.document_schema import DocumentResponse,DocumentType
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from PIL import Image, UnidentifiedImageError
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.services.text_extraction import extract_document
from app.services.invoice_extraction import extract_document_fields
from app.services.invoice_validation_service import validate_invoice_data
from app.models.document_extraction import DocumentExtraction

router = APIRouter(prefix="/documents", tags=["Documents"])

logger = logging.getLogger(__name__)

# Resolve storage relative to the project root, not the terminal's cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = PROJECT_ROOT / "storage" / "documents"

MAX_FILE_SIZE = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

ALLOWED_TYPES = {
    ".pdf": {"mime": "application/pdf", "kind": "pdf"},
    ".png": {"mime": "image/png", "kind": "png"},
    ".jpg": {"mime": "image/jpeg", "kind": "jpeg"},
    ".jpeg": {"mime": "image/jpeg", "kind": "jpeg"},
}


def validate_file_content(file_path: Path, extension: str) -> str:
    """Validate signature and basic file structure."""

    expected = ALLOWED_TYPES[extension]
    detected = filetype.guess(str(file_path))

    if detected is None or detected.mime != expected["mime"]:
        raise HTTPException(
            status_code=400,
            detail="File content does not match its extension",
        )

    try:
        if expected["kind"] == "pdf":
            with fitz.open(file_path) as pdf:
                if pdf.needs_pass:
                    raise HTTPException(
                        status_code=400,
                        detail="Password-protected PDFs are not supported",
                    )

                if pdf.page_count == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="PDF contains no pages",
                    )

        else:
            with Image.open(file_path) as image:
                if image.format != expected["kind"].upper():
                    raise HTTPException(
                        status_code=400,
                        detail="Image format does not match its extension",
                    )

                image.verify()

    except HTTPException:
        raise
    except (fitz.FileDataError, UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="File is malformed or cannot be opened",
        )

    return expected["mime"]


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    file: UploadFile = File(...),
    username: str | None = Form(default=None, max_length=150),
    email: str | None = Form(default=None, max_length=255),
    document_type:DocumentType=Form(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required",
        )

    original_filename = Path(file.filename.replace("\\", "/")).name

    if not original_filename or len(original_filename) > 255:
        raise HTTPException(
            status_code=400,
            detail="Invalid filename",
        )

    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, PNG, JPG and JPEG files are allowed",
        )

    if email is not None:
        try:
            email = str(TypeAdapter(EmailStr).validate_python(email))
        except ValidationError:
            raise HTTPException(
                status_code=422,
                detail="Invalid email address",
            )

    storage_key = f"{uuid.uuid4().hex}{extension}"
    file_path = STORAGE_DIR / storage_key

    file_size = 0
    sha256 = hashlib.sha256()
    committed = False

    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)

        # Exclusive creation avoids accidentally overwriting an existing file.
        with file_path.open("xb") as destination:
            while True:
                chunk = file.file.read(CHUNK_SIZE)

                if not chunk:
                    break

                file_size += len(chunk)

                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Maximum file size is 20 MB",
                    )

                sha256.update(chunk)
                destination.write(chunk)

        if file_size == 0:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty",
            )

        actual_mime_type = validate_file_content(file_path, extension)
        file_hash = sha256.hexdigest()

        # Reject exact duplicate file content.
        existing = db.scalar(
            select(Documents.id).where(Documents.file_hash == file_hash).limit(1)
        )

        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="This document has already been uploaded",
            )

        document = Documents(
            username=username,
            email=email,
            document_type=document_type.value,
            original_filename=original_filename,
            storage_key=f"documents/{storage_key}",
            mime_type=actual_mime_type,
            file_size=file_size,
            file_hash=file_hash,
            status="uploaded",
        )

        db.add(document)
        db.commit()
        committed = True
        db.refresh(document)

        logger.info(
            "Document uploaded: id=%s size=%s",
            document.id,
            file_size,
        )

        return document

    except HTTPException:
        if not committed:
            db.rollback()
        raise

    except IntegrityError as exc:
        if not committed:
            db.rollback()

        # A concurrent request could have inserted the same hash.
        raise HTTPException(
            status_code=409,
            detail="Document conflicts with an existing database record",
        ) from exc

    except SQLAlchemyError as exc:
        if not committed:
            db.rollback()

        logger.exception("Database error while uploading document")

        raise HTTPException(
            status_code=500,
            detail="Failed to save document metadata",
        ) from exc

    except Exception as exc:
        if not committed:
            db.rollback()

        logger.exception("Unexpected error while uploading document")

        raise HTTPException(
            status_code=500,
            detail="Failed to save the document",
        ) from exc

    finally:
        file.file.close()

        # Never remove a file after its database transaction committed.
        if not committed:
            file_path.unlink(missing_ok=True)


@router.get(
    "",
    response_model=list[DocumentResponse],
)
def list_documents(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    statement = (
        select(Documents).order_by(Documents.id.desc()).offset(skip).limit(limit)
    )

    return db.scalars(statement).all()


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = db.get(Documents, document_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document





@router.post("/{document_id}/extract")
def extract_document_text(
    document_id: str,
    db: Session = Depends(get_db),
):
    # 1. Validate document ID
    try:
        doc_id = int(document_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid document ID",
        )

    # 2. Fetch document from database
    document = db.get(Documents, doc_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    # 3. Validate file type
    if not document.storage_key.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Text extraction currently supports PDF files only",
        )

    # 4. Validate supported document type
    if document.document_type not in {"invoice", "resume"}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported document type: {document.document_type}. "
                "Supported types are invoice and resume."
            ),
        )

    try:
        # 5. Extract text from PDF using PyMuPDF / PaddleOCR
        extraction_result = extract_document(document.storage_key)

        extracted_text = extraction_result.get("full_text", "")

        if not extracted_text.strip():
            raise HTTPException(
                status_code=422,
                detail="No text could be extracted from the document",
            )

        # 6. Extract structured data using Groq
        try:
            extracted_data = extract_document_fields(
                extracted_text,
                document.document_type,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        except Exception as exc:
            logger.exception(
                "AI extraction failed for document %s",
                doc_id,
            )
            raise HTTPException(
                status_code=502,
                detail="AI document extraction failed",
            ) from exc

        # 7. Validate invoice data only
        validation_result = None

        if document.document_type == "invoice":
            validation_result = validate_invoice_data(extracted_data)

        # 8. Prepare JSON-compatible data for the database
        extracted_data_json = extracted_data.model_dump(mode="json")

        validation_json = (
            validation_result.model_dump(mode="json")
            if validation_result is not None
            else None
        )

        # 9. Save extraction results in the database
        saved_extraction = DocumentExtraction(
            document_id=document.id,
            document_type=document.document_type,
            extracted_data=extracted_data_json,
            validation_result=validation_json,
        )

        try:
            db.add(saved_extraction)
            db.commit()
            db.refresh(saved_extraction)

        except Exception as exc:
            db.rollback()

            logger.exception(
                "Failed to save extraction for document %s",
                doc_id,
            )

            raise HTTPException(
                status_code=500,
                detail="Failed to save extracted document data",
            ) from exc

        # 10. Return response for invoice or resume
        status_value = (
            "validation_failed"
            if validation_result is not None
            and not validation_result.is_valid
            else "extracted"
        )

        return {
            "document_id": document.id,
            "extraction_id": saved_extraction.id,
            "document_type": document.document_type,
            "status": status_value,
            "extraction": extraction_result,
            "extracted_data": extracted_data_json,
            "validation": validation_json,
        }

    except HTTPException:
        raise

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Stored document file not found",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Document extraction failed for document %s",
            doc_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to extract document text",
        ) from exc





@router.get("/{document_id}/extractions")
def get_document_extractions(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = db.get(Documents, document_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    statement = (
        select(DocumentExtraction)
        .where(DocumentExtraction.document_id == document_id)
        .order_by(DocumentExtraction.id.desc())
    )

    extractions = db.scalars(statement).all()

    return {
        "document_id": document_id,
        "count": len(extractions),
        "extractions": [
            {
                "extraction_id": item.id,
                "document_type": item.document_type,
                "extracted_data": item.extracted_data,
                "validation": item.validation_result,
                "created_at": item.created_at,
            }
            for item in extractions
        ],
    }
