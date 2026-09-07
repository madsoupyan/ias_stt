"""Deployment and location-history API endpoints (/api/...)."""
import os
import uuid
import mimetypes

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy.exc import IntegrityError

from app.auth import require_permission
from app.models.database import db
from app.models.trap import Trap
from app.models.deployment import Deployment
from app.models.deployment_location import DeploymentLocation
from app.models import deployment_location
from app.models.picture import Picture
from app.services import deployment_service
from app.time_utils import format_app_datetime
from app.models.notes import Notes

deployments_bp = Blueprint("deployments", __name__, url_prefix="/api")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif"}
def _error(message, code):
    return jsonify({"error": message}), code


def _upload_dir():
    return os.path.join(current_app.config["DATA_DIR"], "uploads")


def _photo_file_path(photo):
    stored_name = photo.stored_filename
    if not stored_name or stored_name != os.path.basename(stored_name):
        return None

    candidates = [
        os.path.join(_upload_dir(), stored_name),
        os.path.join(current_app.static_folder, "uploads", stored_name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Deployment CRUD
# ---------------------------------------------------------------------------
@deployments_bp.route("/deployments", methods=["GET"])
@require_permission("deployments:read")
def list_deployments():
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return _error("'limit' and 'offset' must be integers", 400)
    if limit < 0 or offset < 0:
        return _error("'limit' and 'offset' must be non-negative", 400)

    query = Deployment.query
    trap_id = request.args.get("trap_id")
    if trap_id:
        query = query.filter(Deployment.trap_id == trap_id)
    status = request.args.get("status")
    if status:
        query = query.filter(Deployment.status == status)

    deps = query.order_by(Deployment.start_date.desc()).limit(limit).offset(offset).all()
    return jsonify([d.to_dict() for d in deps]), 200


@deployments_bp.route("/deployments/<int:dep_id>", methods=["GET"])
@require_permission("deployments:read")
def get_deployment(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)
    return jsonify(dep.to_dict()), 200


@deployments_bp.route("/deployments", methods=["POST"])
@require_permission("deployments:create")
def create_deployment_manual():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Request body must be a JSON object", 400)

    trap_id = data.get("trap_id")
    if not trap_id:
        return _error("Field 'trap_id' is required", 400)

    trap = db.session.get(Trap, trap_id)
    if trap is None:
        return _error("Trap not found", 404)

    try:
        dep = deployment_service.create_deployment(
            trap,
            location=data.get("location"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to manually create deployment")
        return _error("Internal Server Error", 500)

    return jsonify(dep.to_dict()), 201


@deployments_bp.route("/deployments/<int:dep_id>", methods=["PUT"])
@require_permission("deployments:update")
def update_deployment(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Request body must be a JSON object", 400)

    if "animal_capture" in data:
        animal_capture = data["animal_capture"]
        if animal_capture is not None and (
            not isinstance(animal_capture, str) or len(animal_capture) > 255
        ):
            return _error(
                "Field 'animal_capture' must be a string of at most 255 characters",
                400,
            )
        dep.animal_capture = animal_capture

    if "notes" in data and data["notes"] is not None:
        if not isinstance(data["notes"], str) or len(data["notes"]) > 5000:
            return _error(
                "Field 'notes' must be a string of at most 5000 characters",
                400,
            )
        if data["notes"].strip():
            new_note = Notes(deployment_id=dep.id, notes=data["notes"])
            db.session.add(new_note)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to update deployment %s", dep_id)
        return _error("Internal Server Error", 500)

    return jsonify(dep.to_dict()), 200



@deployments_bp.route("/deployments/<int:dep_id>", methods=["DELETE"])
@require_permission("deployments:delete")
def delete_deployment(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)
    try:
        db.session.delete(dep)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete deployment %s", dep_id)
        return _error("Internal Server Error", 500)
    return jsonify({"message": f"Deployment {dep_id} deleted"}), 200


# ---------------------------------------------------------------------------
# Photo upload
# ---------------------------------------------------------------------------
def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@deployments_bp.route("/deployments/<int:dep_id>/photo", methods=["POST"])
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

    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)

    ext = file.filename.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(upload_dir, stored_name)
    file.save(file_path)


    photo_filename = file.filename
    
    new_photo = Picture(
        deployment_id=dep.id,
        photo_url=stored_name,
        photo_filename=photo_filename              
    )
    
    db.session.add(new_photo)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to save photo for deployment %s", dep_id)
        return _error("Internal Server Error", 500)

    return jsonify(dep.to_dict()), 200


@deployments_bp.route(
    "/deployments/<int:dep_id>/photos/<int:photo_id>", methods=["GET"]
)
@require_permission("deployments:read")
def get_photo(dep_id, photo_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    photo = Picture.query.filter_by(id=photo_id, deployment_id=dep_id).first()
    if photo is None:
        return _error("Photo not found", 404)

    file_path = _photo_file_path(photo)
    if file_path is None:
        return _error("Photo file not found", 404)

    mimetype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    return send_file(
        file_path,
        mimetype=mimetype,
        download_name=photo.photo_filename or photo.stored_filename,
        conditional=True,
    )


# ---------------------------------------------------------------------------
# Trap-scoped nested endpoints
# ---------------------------------------------------------------------------
@deployments_bp.route("/traps/<int:trap_id>/deployments", methods=["GET"])
@require_permission("deployments:read")
def trap_deployments(trap_id):
    trap = db.session.get(Trap, trap_id)
    if trap is None:
        return _error("Trap not found", 404)

    deps = (
        trap.deployments
        .order_by(Deployment.start_date.desc())
        .all()
    )
    return jsonify([d.to_dict() for d in deps]), 200


@deployments_bp.route("/traps/<int:trap_id>/deployments/active", methods=["GET"])
@require_permission("deployments:read")
def trap_active_deployment(trap_id):
    trap = db.session.get(Trap, trap_id)
    if trap is None:
        return _error("Trap not found", 404)

    active = trap.get_active_deployment()
    if active is None:
        return _error("No active deployment", 404)

    return jsonify(active.to_dict()), 200


# ---------------------------------------------------------------------------
# Location history
# ---------------------------------------------------------------------------
@deployments_bp.route("/deployments/<int:dep_id>/locations", methods=["GET"])
@require_permission("deployments:read")
def deployment_locations(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    locs = (
        dep.locations
        .order_by(DeploymentLocation.recorded_at.desc())
        .all()
    )
    return jsonify([l.to_dict() for l in locs]), 200


@deployments_bp.route("/deployments/<int:dep_id>/locations/latest", methods=["GET"])
@require_permission("deployments:read")
def deployment_latest_location(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    loc = (
        dep.locations
        .order_by(DeploymentLocation.recorded_at.desc())
        .first()
    )
    if loc is None:
        return _error("No location recorded", 404)

    return jsonify(loc.to_dict()), 200

@deployments_bp.route("/traps/<int:trap_id>/locations", methods=["GET"])
@require_permission("deployments:read")
def trap_locations(trap_id):
    trap = db.session.get(Trap, trap_id)
    if trap is None:
        return _error("Trap not found", 404)

    locs = deployment_service.get_location_history(trap)
    return jsonify([l.to_dict() for l in locs]), 200
