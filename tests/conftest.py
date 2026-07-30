import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_gigaloom_user_state(tmp_path_factory):
    """Keep the test session away from real GigaLoom user state."""
    canonical = "GIGALOOM_DATA_DIR"
    legacy = "GPT2GIGA_HARNESS_DATA_DIR"
    previous_canonical = os.environ.get(canonical)
    previous_legacy = os.environ.get(legacy)
    os.environ[canonical] = str(tmp_path_factory.mktemp("gigaloom-state"))
    os.environ.pop(legacy, None)
    try:
        yield
    finally:
        if previous_canonical is None:
            os.environ.pop(canonical, None)
        else:
            os.environ[canonical] = previous_canonical
        if previous_legacy is None:
            os.environ.pop(legacy, None)
        else:
            os.environ[legacy] = previous_legacy
