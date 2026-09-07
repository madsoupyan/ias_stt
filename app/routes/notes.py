"""CRUD API for Notes (/api/notes)."""
from flask import Blueprint, current_app, jsonify, request
from app.auth import require_permission
from app.models.database import db
from app.models.deployment import Deployment
from app.models.notes import Notes

note_bp = Blueprint("notes", __name__, url_prefix="/api")


def _error(message, code):
    return jsonify({"error": message}), code


@note_bp.route("/deployments/<int:dep_id>/notes", methods=["POST"])
@require_permission("deployments:update")
def upload_note(dep_id):
    dep = db.session.get(Deployment, dep_id)
    if dep is None:
        return _error("Deployment not found", 404)

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict) or "notes" not in data:
        return _error("Field 'notes' is required in JSON body", 400)
    if not isinstance(data["notes"], str) or not data["notes"].strip():
        return _error("Field 'notes' must be a non-empty string", 400)
    if len(data["notes"]) > 5000:
        return _error("Field 'notes' exceeds max length of 5000", 400)

    try:
        new_note = Notes(
            deployment_id=dep.id,
            notes=data["notes"],
        )
        db.session.add(new_note)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to save note for deployment %s", dep_id)
        return _error("Internal Server Error", 500)

    return jsonify(dep.to_dict()), 200
