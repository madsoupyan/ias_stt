"""Local-user JWT authentication endpoints."""
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
)

from app.auth import (
    VALID_ROLES,
    auth_error,
    permissions_for_role,
    require_jwt,
    require_refresh_jwt,
)
from app.models.user import User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _claims_for_user(user):
    return {
        "role": user.role,
        "display_name": user.display_name,
        "email": user.email,
    }


def _seconds(value):
    return int(value.total_seconds()) if hasattr(value, "total_seconds") else int(value)


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _user_response(user):
    return user.to_public_dict(permissions_for_role(user.role))


def _invalid_credentials():
    return auth_error(
        "INVALID_CREDENTIALS",
        "Invalid username or password.",
        401,
    )


@auth_bp.route("/login", methods=["POST"])
def login():
    if not request.is_json:
        return auth_error("INVALID_REQUEST", "Request body must be JSON.", 400)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return auth_error("INVALID_REQUEST", "Request body must be a JSON object.", 400)

    username = data.get("username")
    password = data.get("password")
    if not isinstance(username, str) or not username.strip():
        return _invalid_credentials()
    if not isinstance(password, str) or not password:
        return _invalid_credentials()

    user = User.query.filter_by(username=username.strip()).first()
    if user is None or not user.is_active or not user.check_password(password):
        return _invalid_credentials()
    if user.role not in VALID_ROLES:
        return auth_error(
            "APPLICATION_ACCESS_DENIED",
            "This account is not authorized to use the application.",
            403,
        )

    claims = _claims_for_user(user)
    access_token = create_access_token(
        identity=user.username,
        additional_claims=claims,
    )
    refresh_token = create_refresh_token(
        identity=user.username,
        additional_claims=claims,
    )

    now = datetime.now(timezone.utc)
    access_seconds = _seconds(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"])
    refresh_seconds = _seconds(current_app.config["JWT_REFRESH_TOKEN_EXPIRES"])
    access_expiry = now.timestamp() + access_seconds
    refresh_expiry = now.timestamp() + refresh_seconds
    return jsonify(
        {
            "authenticated": True,
            "user": _user_response(user),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": access_seconds,
            "refresh_expires_in": refresh_seconds,
            "expires_at": _iso_timestamp(access_expiry),
            "refresh_expires_at": _iso_timestamp(refresh_expiry),
        }
    ), 200


@auth_bp.route("/me", methods=["GET"])
@require_jwt
def me():
    claims = get_jwt()
    username = get_jwt_identity()
    role = claims["role"]
    return jsonify(
        {
            "authenticated": True,
            "user": {
                "username": username,
                "display_name": claims.get("display_name"),
                "email": claims.get("email"),
                "role": role,
                "permissions": permissions_for_role(role),
            },
            "expires_at": _iso_timestamp(claims["exp"]),
        }
    ), 200


@auth_bp.route("/refresh", methods=["POST"])
@require_refresh_jwt
def refresh():
    username = get_jwt_identity()
    user = User.query.filter_by(username=username).first()
    if user is None or not user.is_active or user.role not in VALID_ROLES:
        return auth_error(
            "AUTHENTICATION_REQUIRED",
            "Authentication is required.",
            401,
        )

    access_seconds = _seconds(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"])
    access_token = create_access_token(
        identity=user.username,
        additional_claims=_claims_for_user(user),
    )
    return jsonify(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": access_seconds,
        }
    ), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # JWTs are intentionally stateless. The client discards its access and
    # refresh tokens; a copied token remains valid until its expiration.
    return "", 204
