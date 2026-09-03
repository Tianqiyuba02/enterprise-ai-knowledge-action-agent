from collections.abc import Iterator
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.application import create_app
from app.llm.client import GeminiStructuredClient
from app.repositories.demo import DemoRepository


@pytest.fixture
def mocked_llm_client() -> Mock:
    return Mock(spec=GeminiStructuredClient)


@pytest.fixture
def api_client(mocked_llm_client: Mock) -> Iterator[TestClient]:
    app = create_app(
        repository=DemoRepository(),
        llm_client=cast(GeminiStructuredClient, mocked_llm_client),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
