from __future__ import annotations


def render_client_fixture(*, tests_style: str, app_fixture_name: str = "app") -> str:
    style = str(tests_style or "sync").strip().lower()
    if style == "async":
        return (
            "import pytest\n"
            "import httpx\n\n\n"
            "@pytest.fixture\n"
            f"async def client({app_fixture_name}, dependency_overrides_registry):\n"
            f"    async with httpx.AsyncClient(app={app_fixture_name}, base_url='http://test') as test_client:\n"
            "        yield test_client\n"
        )

    return (
        "import pytest\n"
        "from fastapi.testclient import TestClient\n\n\n"
        "@pytest.fixture\n"
        f"def client({app_fixture_name}, dependency_overrides_registry):\n"
        f"    with TestClient({app_fixture_name}) as test_client:\n"
        "        yield test_client\n"
    )
