# --- START OF FILE backend/bulk_cert.py ---
# Bulk certificate job creation and execution
import os
import uuid
from datetime import datetime, timezone

from common import get_config_dir, get_cert_dir, purge_old_logs, DEFAULT_LOG_RETENTION_DAYS
from database import persist_job, load_job
from certificates import build_certificate, build_bulk_certificate_html
from notifier import send_slack_notification
from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK, logger

def create_bulk_cert_job(job_ids):
    """
    Create a bulk certificate generation job for multiple completed erase jobs.
    
    Args:
        job_ids: List of job IDs (or friendly IDs) to generate certificates for
        
    Returns:
        Tuple of (job_dict, error_dict, status_code) on validation error,
        or (job_dict, None, None) on success
    """
    # Validate input is a list and enforce size limit for DoS prevention
    if not isinstance(job_ids, list):
        return None, {"error": "job_ids must be a list"}, 400
    if len(job_ids) == 0:
        return None, {"error": "job_ids cannot be empty"}, 400
    if len(job_ids) > 100:
        return None, {"error": "job_ids exceeds maximum limit of 100"}, 400
    
    # Detect duplicate job IDs
    seen_ids = set()
    for job_id in job_ids:
        if job_id in seen_ids:
            return None, {"error": f"duplicate job_id detected: {job_id}"}, 400
        seen_ids.add(job_id)
    
    # Validate each job exists and is a completed erase job
    validated_jobs = []
    for job_id in job_ids:
        if not isinstance(job_id, str) or not job_id.strip():
            return None, {"error": f"invalid job_id in list: {job_id}"}, 400
        
        # Load job from database
        job = load_job(job_id.strip())
        if not job:
            return None, {"error": f"job not found: {job_id}"}, 404
        
        # Validate job type is 'erase'
        if job.get("job_type") != "erase":
            return None, {"error": f"job {job_id} is not an erase job (type: {job.get('job_type')})"}, 400
        
        # Validate job status is completed
        if job.get("status") != "completed":
            return None, {"error": f"job {job_id} is not completed (status: {job.get('status')})"}, 400
        
        validated_jobs.append(job)
    
    now = datetime.now(timezone.utc).isoformat()
    job_id = str(uuid.uuid4())
    
    # Generate a human-readable friendly_id for bulk cert jobs
    # Format: BULK-YYYYMMDD-XXXX (where XXXX is a random 4-digit hex)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_suffix = uuid.uuid4().hex[:4].upper()
    friendly_id = f"BULK-{date_str}-{random_suffix}"
    
    bulk_job = {
        "id": job_id,
        "friendly_id": friendly_id,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
        "verification": None,
        "marker": None,
        "certificate": None,
        "progress_percent": 0.0,
        "current_phase": "Queued in Line",
        "job_type": "bulk_cert",
        "request": {
            "target_job_ids": job_ids,
            "total_jobs": len(job_ids),
        },
    }
    
    # Store in memory with lock for thread safety
    with BULK_CERT_JOBS_LOCK:
        BULK_CERT_JOBS[job_id] = bulk_job
    
    # Persist to database
    persist_job(bulk_job)
    
    return bulk_job, None, None

def run_bulk_cert_job(job_id):
    """
    Execute a bulk certificate generation job.
    
    This function generates certificates for multiple completed erase jobs and
    creates a bulk HTML file containing all certificates. Uses locks to ensure
    thread-safe operation when multiple bulk cert jobs run simultaneously.
    
    Args:
        job_id: The ID of the bulk certificate job to run
    """
    with BULK_CERT_JOBS_LOCK:
        job = BULK_CERT_JOBS.get(job_id)
        if not job:
            logger.error(f"Bulk cert job {job_id} not found in memory")
            return
        job["status"] = "running"
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["progress_percent"] = 0.0
        job["current_phase"] = "Initializing bulk certificate generation"
        persist_job(job)
    
    send_slack_notification(job, "running")
    
    target_job_ids = job["request"].get("target_job_ids", [])
    total_jobs = len(target_job_ids)
    certificates = []
    failed_jobs = []
    
    logger.info(f"Bulk cert job {job_id} starting: generating certificates for {total_jobs} jobs")
    
    for idx, target_job_id in enumerate(target_job_ids):
        # Update progress
        progress = (idx / total_jobs) * 100
        with BULK_CERT_JOBS_LOCK:
            job = BULK_CERT_JOBS.get(job_id)
            if job:
                job["progress_percent"] = round(progress, 1)
                job["current_phase"] = f"Generating certificate {idx + 1}/{total_jobs}"
                persist_job(job)
        
        try:
            # Load the target job from database
            target_job = load_job(target_job_id)
            if not target_job:
                logger.warning(f"Bulk cert job {job_id}: target job {target_job_id} not found")
                failed_jobs.append({"job_id": target_job_id, "error": "job_not_found"})
                continue
            
            # Generate certificate for this job
            # Note: build_certificate uses SIGNATURE_KDF_ITERATIONS from certificates.py
            # ensuring cryptographic parameter consistency across code paths
            certificate = build_certificate(target_job)
            certificates.append(certificate)
            logger.info(f"Bulk cert job {job_id}: generated certificate for job {target_job_id}")
            
        except Exception as e:
            logger.error(f"Bulk cert job {job_id}: failed to generate certificate for job {target_job_id}: {str(e)}")
            failed_jobs.append({"job_id": target_job_id, "error": str(e)})
    
    # Update progress to completion
    with BULK_CERT_JOBS_LOCK:
        job = BULK_CERT_JOBS.get(job_id)
        if job:
            job["progress_percent"] = 100.0
            job["current_phase"] = "Generating bulk HTML"
            persist_job(job)
    
    # Generate bulk HTML file
    bulk_html_path = None
    bulk_html_filename = None
    try:
        # Validate certificate data before generating bulk HTML
        required_fields = ["id", "friendly_id", "issued_at", "verification"]
        for idx, cert in enumerate(certificates):
            for field in required_fields:
                if field not in cert:
                    raise ValueError(f"Certificate {idx} missing required field: {field}")
        
        bulk_html_content = build_bulk_certificate_html(certificates)
        bulk_html_filename = f"bulk-cert-{job_id}.html"
        bulk_html_path = os.path.join(get_cert_dir(), bulk_html_filename)
        with open(bulk_html_path, "w", encoding="utf-8") as f:
            f.write(bulk_html_content)
        logger.info(f"Bulk cert job {job_id}: generated bulk HTML at {bulk_html_path}")
    except Exception as e:
        logger.error(f"Bulk cert job {job_id}: failed to generate bulk HTML: {str(e)}")
    
    # Finalize job status
    with BULK_CERT_JOBS_LOCK:
        job = BULK_CERT_JOBS.get(job_id)
        if not job:
            return
        
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        job["result"] = {
            "total_jobs": total_jobs,
            "successful_certificates": len(certificates),
            "failed_jobs": failed_jobs,
            "bulk_html": {
                "filename": bulk_html_filename,
                "path": bulk_html_path,
            } if bulk_html_path else None,
        }
        
        if failed_jobs:
            job["status"] = "partial_success"
            job["error"] = f"Completed with {len(failed_jobs)} failures out of {total_jobs} jobs"
        else:
            job["status"] = "completed"
            job["error"] = None
        
        job["certificate"] = {
            "bulk_html_path": bulk_html_path,
            "bulk_html_filename": bulk_html_filename,
            "individual_certificates": [c.get("path") for c in certificates],
        }
        
        persist_job(job)
    
    send_slack_notification(job)
    
    if job["status"] == "completed":
        logger.info(f"Bulk cert job {job_id} COMPLETED: generated {len(certificates)} certificates")
    else:
        logger.warning(f"Bulk cert job {job_id} PARTIAL SUCCESS: {len(certificates)} successful, {len(failed_jobs)} failed")
    
    try:
        purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
    except Exception as e:
        logger.warning(f"Failed to purge old logs: {e}")
# --- END OF FILE backend/bulk_cert.py ---
