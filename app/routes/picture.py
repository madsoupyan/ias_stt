"""Compatibility picture-upload endpoint."""
import os
import uuid
from flask import Blueprint, current_app, jsonify, request
from app.auth import require_permission
from app.models.database import db
from app.models.deployment import Deployment
from app.models.picture import Picture

picture_bp = Blueprint("picture", __name__, url_prefix="/api")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif"}
def _error(message, code):
    return jsonify({"error": message}), code

def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@picture_bp.route("/deployments/<int:dep_id>/picture", methods=["POST"])
@require_permission("deployment_photos:create")
def upload_photo(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    if "file" not in request.files:
        return _error("No file provided", 400)

    file = request.files["file"]
    if file.filename == "":
        return _error("No file selected", 400)

    if not _allowed_file(file.filename):
        return _error("File type not allowed (jpg, jpeg, png, gif)", 400)

    upload_dir = os.path.join(current_app.config["DATA_DIR"], "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    ext = file.filename.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(upload_dir, stored_name)
    file.save(file_path)

    db.session.add(
        Picture(
            deployment_id=dep.id,
            photo_url=stored_name,
            photo_filename=file.filename,
        )
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to save photo for deployment %s", dep_id)
        return _error("Internal Server Error", 500)

    return jsonify(dep.to_dict()), 200
