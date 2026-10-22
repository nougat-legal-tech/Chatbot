"""Guard that this sidecar stays an exact copy of django-hub's MCP server.

django-hub is the source of truth for the callable-function map. Skips when the
django-hub checkout is not beside this repo (for example in Chatbot-only CI).
"""

from pathlib import Path

import pytest

HERE = Path(__file__).parent
DJANGO_HUB = HERE.parents[1] / "django-hub"
PAIRS = (
    ("server.py", "mcp_server/server.py", 0),
    ("otter_client.py", "mcp_server/otter_client.py", 0),
    ("function_taxonomy.py", "jurist_backend/core/services/function_taxonomy.py", 0),
    # The vendored registry carries one provenance comment line at the top.
    ("callable_function_registry.py", "jurist_backend/core/services/callable_function_registry.py", 1),
)


def _lines(path: Path, skip: int) -> list[str]:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")[skip:]


@pytest.mark.skipif(not DJANGO_HUB.is_dir(), reason="django-hub checkout not available")
@pytest.mark.parametrize(("vendored", "source", "skip"), PAIRS)
def test_vendored_file_matches_django_hub(vendored, source, skip):
    assert _lines(HERE / vendored, skip) == _lines(DJANGO_HUB / source, 0), (
        f"Chatbot/mcp_server/{vendored} drifted from django-hub/{source}; re-copy it."
    )
