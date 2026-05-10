import os
import shutil
import tempfile


def pytest_configure():
    """Set SCHEMA_DIR to a temp directory so tests don't touch the real schema files."""
    tmpdir = tempfile.mkdtemp(prefix="fpl_schema_test_")
    os.environ["SCHEMA_DIR"] = tmpdir


def pytest_unconfigure():
    """Clean up the temp schema directory."""
    tmpdir = os.environ.pop("SCHEMA_DIR", None)
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
