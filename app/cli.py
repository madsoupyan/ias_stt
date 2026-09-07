"""Flask CLI commands for managing local users."""
import click

from app.auth import VALID_ROLES
from app.models.database import db
from app.models.user import User


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(VALID_ROLES), required=True)
    @click.option("--display-name", default=None)
    @click.option("--email", default=None)
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    )
    def create_user(username, role, display_name, email, password):
        """Create or update a local JWT user."""
        existing = User.query.filter_by(username=username).first()
        user = existing or User(username=username)
        user.role = role
        user.display_name = display_name
        user.email = email
        user.is_active = True
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"User {username} is ready with role {role}.")
