from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_globals() -> None:
    """Reset module-level globals between tests."""
    try:
        from openplan.core.embedding import reset_embeddings

        reset_embeddings()
    except ImportError:
        pass
    try:
        from openplan.core.activation import reset_cache

        reset_cache()
    except ImportError:
        pass
