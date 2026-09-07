"""Administrator-only CRUD API for local application users."""
from flask import Blueprint, jsonify, request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.auth import VALID_ROLES, auth_error, current_actor, require_user_permission
from app.models.database import db
from app.models.user import User


users_bp = Blueprint("users", __name__, url_prefix="/api/users")

MAX_PASSWORD_LENGTH = 255
MIN_PASSWORD_LENGTH = 8
USER_FIELDS = {"display_name", "email", "role", "password", "is_active"}


def _not_found():
    return auth_error("NOT_FOUND", "User not found.", 404)


def _validate_text(data, field, max_length):
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        return f"Field '{field}' must be a string of at most {max_length} characters"
    return None


def _validate_user_data(data, creating=False):
    if not isinstance(data, dict):
        return "Request body must be a JSON object"

    unknown = set(data) - {"username"} - USER_FIELDS
    if unknown:
        return f"Unknown field(s): {', '.join(sorted(unknown))}"

    if creating:
        username = data.get("username")
        if not isinstance(username, str) or not username.strip():
            return "Field 'username' is required and must be a non-empty string"
        if len(username.strip()) > 150:
            return "Field 'username' exceeds max length of 150"
        if not isinstance(data.get("password"), str) or not data["password"]:
            return "Field 'password' is required and must be a non-empty string"
        if data.get("role") not in VALID_ROLES:
            return f"Field 'role' must be one of: {', '.join(VALID_ROLES)}"

    if "password" in data and data["password"] is not None:
        password = data["password"]
        if not isinstance(password, str):
            return "Field 'password' must be a string"
        if len(password) < MIN_PASSWORD_LENGTH:
            return f"Field 'password' must be at least {MIN_PASSWORD_LENGTH} characters"
        if len(password) > MAX_PASSWORD_LENGTH:
            return f"Field 'password' exceeds max length of {MAX_PASSWORD_LENGTH}"

    for field, max_length in (("display_name", 255), ("email", 255)):
        error = _validate_text(data, field, max_length)
        if error:
            return error

    if "role" in data and data["role"] not in VALID_ROLES:
        return f"Field 'role' must be one of: {', '.join(VALID_ROLES)}"

    if "is_active" in data and not isinstance(data["is_active"], bool):
        return "Field 'is_active' must be a boolean"

    return None


def _validation_error(message):
    return auth_error("VALIDATION_ERROR", message, 422)


def _active_admin_count(exclude_user_id=None):
    query = User.query.filter_by(role="administrator", is_active=True)
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def _protect_admin_lockout(target, data):
    actor = current_actor()
    is_self = target.username == actor
    becoming_inactive = data.get("is_active") is False
    losing_admin_role = (
        data.get("role") is not None and data.get("role") != "administrator"
    )

    if is_self and (becoming_inactive or losing_admin_role):
        return auth_error(
            "CONFLICT",
            "You cannot deactivate or demote your own administrator account.",
            409,
        )

    target_will_lose_admin = target.role == "administrator" and (
        becoming_inactive or losing_admin_role
    )
    if target_will_lose_admin and _active_admin_count(target.id) == 0:
        return auth_error(
            "CONFLICT",
            "At least one active administrator must remain.",
            409,
        )
    return None


@users_bp.route("", methods=["GET"])
@require_user_permission("settings:admin")
def list_users():
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return _validation_error("'limit' and 'offset' must be integers")
    if limit < 0 or offset < 0:
        return _validation_error("'limit' and 'offset' must be non-negative")

    query = User.query
    search = request.args.get("search", "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )

    users = query.order_by(User.id).limit(limit).offset(offset).all()
    return jsonify([user.to_dict() for user in users]), 200


@users_bp.route("/<int:user_id>", methods=["GET"])
@require_user_permission("settings:admin")
def get_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return _not_found()
    return jsonify(user.to_dict()), 200


@users_bp.route("", methods=["POST"])
@require_user_permission("settings:admin")
def create_user():
    data = request.get_json(silent=True)
    error = _validate_user_data(data, creating=True)
    if error:
        return _validation_error(error)

    username = data["username"].strip()
    if User.query.filter_by(username=username).first() is not None:
        return auth_error(
            "CONFLICT",
            f"Username '{username}' already exists.",
            409,
        )

    user = User(
        username=username,
        display_name=data.get("display_name"),
        email=data.get("email"),
        role=data["role"],
        is_active=data.get("is_active", True),
    )
    user.set_password(data["password"])
    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return auth_error(
            "CONFLICT",
            f"Username '{username}' already exists.",
            409,
        )

    return jsonify(user.to_dict()), 201


@users_bp.route("/<int:user_id>", methods=["PUT"])
@require_user_permission("settings:admin")
def update_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return _not_found()

    data = request.get_json(silent=True)
    error = _validate_user_data(data)
    if error:
        return _validation_error(error)
    if "username" in data:
        return _validation_error("Field 'username' cannot be changed")

    lockout_error = _protect_admin_lockout(user, data)
    if lockout_error is not None:
        return lockout_error

    for field in ("display_name", "email", "role", "is_active"):
        if field in data:
            setattr(user, field, data[field])
    if "password" in data and data["password"] is not None:
        user.set_password(data["password"])

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return auth_error("INTERNAL_ERROR", "Unable to update user.", 500)

    return jsonify(user.to_dict()), 200


@users_bp.route("/<int:user_id>", methods=["DELETE"])
@require_user_permission("settings:admin")
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return _not_found()
    if user.username == current_actor():
        return auth_error("CONFLICT", "You cannot delete your own account.", 409)
    if user.role == "administrator" and _active_admin_count(user.id) == 0:
        return auth_error(
            "CONFLICT",
            "At least one active administrator must remain.",
            409,
        )

    try:
        db.session.delete(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return auth_error("INTERNAL_ERROR", "Unable to delete user.", 500)

    return jsonify({"message": f"User {user_id} deleted"}), 200
