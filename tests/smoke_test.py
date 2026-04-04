import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import Project, User


def test_app():
    app = create_app()
    with app.app_context():
        users_count = User.query.count()
        projects_count = Project.query.count()
        assert users_count >= 0
        assert projects_count >= 0


if __name__ == "__main__":
    try:
        test_app()
        sys.exit(0)
    except AssertionError:
        sys.exit(1)
