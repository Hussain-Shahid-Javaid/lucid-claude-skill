import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lucid.core import Lucid  # noqa: E402
from lucid.util import FakeClock  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def dbpath(tmp_path):
    return str(tmp_path / "lucid.db")


@pytest.fixture
def L(dbpath, clock):
    lu = Lucid(dbpath, clock)
    yield lu
    lu.close()


def fixture_bytes(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()
