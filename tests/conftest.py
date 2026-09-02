import pytest

from troth import memory as M


@pytest.fixture
def mem(tmp_path):
    """A real Sibyl database, per test, thrown away afterwards.

    Nothing here is mocked. These tests exercise the same SDK calls the
    product runs on, so a breaking change in sibyl-memory-client fails
    the suite rather than surfacing in the demo.
    """
    return M.connect(str(tmp_path / "test.db"))


@pytest.fixture
def seeded(mem):
    M.remember_pricing_rule(mem, M.MAX_DISCOUNT_KEY, 15)
    return mem
