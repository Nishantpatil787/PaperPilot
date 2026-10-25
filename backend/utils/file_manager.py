import os
import json
import time
import uuid
import zipfile
import shutil
import logging
from pathlib import Path
from werkzeug.utils import secure_filename
from config import UPLOAD_FOLDER, OUTPUT_FOLDER, FILE_RETENTION_SECONDS

logger = logging.getLogger("paperpilot.jobs")

# In-memory thread-safe registry cache
_JOB_REGISTRY = {}
_LAST_CLEANUP_TIME = 0.0
CLEANUP_INTERVAL_SECONDS = 600  # Run cleanup at most once every 10 minutes


def create_job(operation=None):
    """
    Creates a new unique job with dedicated upload and output directories.
    Initializes metadata tracking in the job registry.
    """
    job_id = str(uuid.uuid4())
    job_upload_dir = (UPLOAD_FOLDER / job_id).resolve()
    job_output_dir = (OUTPUT_FOLDER / job_id).resolve()

    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)

    job_record = {
        "job_id": job_id,
        "operation": operation or "unknown",
        "status": "processing",
        "output_path": None,
        "output_filename": None,
        "file_type": "application/pdf",
        "created_at": time.time(),
        "metadata": {}
    }

    _JOB_REGISTRY[job_id] = job_record
    return job_id, job_upload_dir, job_output_dir


def register_job(job_id, operation, output_path, output_filename=None, file_type="application/pdf", status="completed", metadata=None):
    """
    Registers a completed processing job with its actual output file path,
    human-readable filename, mime type, and metadata.
    Persists job metadata to job.json in the job directory and updates cache.
    """
    out_p = Path(output_path).resolve()
    if not output_filename:
        output_filename = out_p.name

    job_record = {
        "job_id": job_id,
        "operation": operation,
        "status": status,
        "output_path": str(out_p),
        "output_filename": str(output_filename),
        "file_type": file_type,
        "created_at": time.time(),
        "metadata": metadata or {}
    }

    _JOB_REGISTRY[job_id] = job_record

    # Persist metadata to job.json inside job's output directory
    job_dir = OUTPUT_FOLDER / job_id
    if job_dir.exists():
        try:
            meta_file = job_dir / "job.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(job_record, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist job.json for job {job_id}: {e}")

    return job_record


def get_job(job_id):
    """
    Retrieves job metadata by job_id.
    First checks in-memory cache, then checks job.json on disk.
    If no metadata file exists, falls back to inspecting the job output directory.
    """
    if not job_id:
        return None

    # Check cache first
    if job_id in _JOB_REGISTRY:
        record = _JOB_REGISTRY[job_id]
        if record.get("output_path") and Path(record["output_path"]).exists():
            return record

    job_dir = OUTPUT_FOLDER / job_id
    if not job_dir.exists():
        return None

    # Try loading job.json from disk
    meta_file = job_dir / "job.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                record = json.load(f)
                _JOB_REGISTRY[job_id] = record
                return record
        except Exception as e:
            logger.warning(f"Failed to read job.json for job {job_id}: {e}")

    # Fallback inspection: find any generated file in job_dir (excluding job.json)
    try:
        candidates = [f for f in job_dir.iterdir() if f.is_file() and f.name != "job.json"]
        if candidates:
            # Sort by modified time descending to pick latest output
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            chosen = candidates[0]
            record = {
                "job_id": job_id,
                "operation": "inferred",
                "status": "completed",
                "output_path": str(chosen.resolve()),
                "output_filename": chosen.name,
                "file_type": "application/zip" if chosen.suffix.lower() == ".zip" else "application/pdf",
                "created_at": chosen.stat().st_mtime,
                "metadata": {}
            }
            _JOB_REGISTRY[job_id] = record
            return record
    except Exception as e:
        logger.warning(f"Error scanning job directory for job {job_id}: {e}")

    return None


def save_upload_file(file, job_upload_dir):
    """
    Saves an uploaded file to the job upload directory using secure filename.
    Returns the resolved Path of the saved file.
    """
    original_name = getattr(file, "filename", None) or "document.pdf"
    safe_name = secure_filename(original_name)
    if not safe_name:
        safe_name = "document.pdf"

    file_path = (job_upload_dir / safe_name).resolve()
    file.save(str(file_path))
    return file_path


def create_zip(files, zip_path):
    """
    Packages a list of files into a ZIP archive.
    """
    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            zipf.write(str(file), arcname=Path(file).name)
    return zip_path


def cleanup_old_files(force=False):
    """
    Safely cleans up temporary job directories older than FILE_RETENTION_SECONDS.
    Throttled so it executes at most once every CLEANUP_INTERVAL_SECONDS unless force=True.
    Never deletes active or recently created job files.
    """
    global _LAST_CLEANUP_TIME
    now = time.time()

    if not force and (now - _LAST_CLEANUP_TIME) < CLEANUP_INTERVAL_SECONDS:
        return

    _LAST_CLEANUP_TIME = now
    min_retention = max(3600, int(FILE_RETENTION_SECONDS))

    for base in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
        if not base.exists():
            continue
        try:
            for folder in base.iterdir():
                if folder.is_dir():
                    try:
                        folder_mtime = folder.stat().st_mtime
                        age = now - folder_mtime
                        if age > min_retention:
                            shutil.rmtree(folder, ignore_errors=True)
                            _JOB_REGISTRY.pop(folder.name, None)
                    except Exception as err:
                        logger.debug(f"Could not clean folder {folder}: {err}")
        except Exception as err:
            logger.debug(f"Could not iterate base {base}: {err}")
