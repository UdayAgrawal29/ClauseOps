from pathlib import Path
import shutil
import uuid
from app.config import UPLOAD_DIR

def save_upload(file_obj, filename: str):
    """
    Save an uploaded file-like object to uploads/ with a unique name.
    file_obj should be binary file-like (e.g., UploadFile.file or open(..., "rb")).
    Returns: saved_path (Path), file_type (extension)
    """
    ext = Path(filename).suffix.lower()
    uid = uuid.uuid4().hex
    saved_name = f"{uid}{ext}"
    saved_path = UPLOAD_DIR / saved_name

    # write file_obj to disk
    # file_obj may be SpooledTemporaryFile (fastapi UploadFile.file)
    with open(saved_path, "wb") as out_f:
        file_obj.seek(0)
        shutil.copyfileobj(file_obj, out_f)

    return saved_path, ext.lstrip(".")