import os
import tempfile

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file.name}"


def pytest_unconfigure(config):
    try:
        os.unlink(_db_file.name)
    except OSError:
        pass
