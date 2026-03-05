from flask import Blueprint, jsonify
from api.db import get_job, get_analysis_by_id

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.get("/jobs/<job_id>")
def get_job_status(job_id: str):
    """Poll job status. When status='done', result summary is included."""
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    response = {
        "id": job["id"],
        "stock_symbol": job["stock_symbol"],
        "job_type": job["job_type"],
        "status": job["status"],          # queued | running | done | failed
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error_message": job.get("error_message"),
    }

    if job["status"] == "done" and job.get("result_id"):
        response["result_id"] = job["result_id"]
        # Attach a lightweight result summary so the client doesn't need a second request
        result = get_analysis_by_id(job["result_id"])
        if result:
            response["result"] = result

    return jsonify(response)
