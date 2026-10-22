import hashlib
import uuid
from pathlib import Path

from app.database.database import get_db
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from app.models.documents import Documents
from app.schemas.document_schema import DocumentResponse
from sqlalchemy.orm import Session

router = APIRouter(prefix="/documents", tags=["Documents"])

STORAGE_DIR = Path("storage")

MAX_FILE_SIZE = 20 * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    file: UploadFile = File(...),
    username: str | None = Form(default=None, max_length=150),
    email: str | None = Form(default=None, max_length=255),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "Filename is required")

    original_filename = Path(file.filename).name

    if len(original_filename) > 255:
        raise HTTPException(400, "Filename is too long")

    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF, PNG, JPG and JPEG files are allowed")

    if email is not None:
        from pydantic import EmailStr, TypeAdapter, ValidationError

        try:
            email = TypeAdapter(EmailStr).validate_python(email)
        except ValidationError:
            raise HTTPException(422, "Invalid email address")

    storage_key = f"documents/{uuid.uuid4().hex}{extension}"
    file_path = STORAGE_DIR / storage_key
    file_size = 0
    sha256 = hashlib.sha256()
    saved_to_db = False

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with file_path.open("wb") as destination:
            while True:
                chunk = file.file.read(1024 * 1024)

                if not chunk:
                    break

                file_size += len(chunk)

                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(413, "Maximum file size is 20 MB")

                sha256.update(chunk)
                destination.write(chunk)

        if file_size == 0:
            raise HTTPException(400, "Uploaded file is empty")

        document = Documents(
            username=username,
            email=email,
            original_filename=original_filename,
            storage_key=storage_key,
            mime_type=file.content_type or "application/octet-stream",
            file_size=file_size,
            file_hash=sha256.hexdigest(),
            status="uploaded",
        )

        db.add(document)
        db.commit()
        db.refresh(document)
        saved_to_db = True

        return document

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "Failed to save the document") from exc

    finally:
        file.file.close()

        if not saved_to_db:
            file_path.unlink(missing_ok=True)
