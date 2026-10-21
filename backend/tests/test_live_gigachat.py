"""Live tests against the real GigaChat API.

Deselected by default via `addopts = "-m 'not live'"`, because every call costs
money and needs a real key. Run them explicitly:

    SBER_API_KEY=<real-key> SBER_API_SCOPE=PERS poetry run pytest -m live

The GigaChat certificate is read from ``res/gigachat-ca.cer`` by a *relative*
path, so these must run with CWD == ``backend/``. The PERS client also serialises
requests through a Redis lock, so the fixtures start a Redis test container to
stand in for the Compose ``redis`` host, which a host-side run cannot resolve.

None of this is needed for the normal suite: `SBER_API_KEY=mock` swaps in
`MockProvider`, which is what every other test uses.
"""

import os
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.live


def _require_real_key():
    import os

    key = os.environ.get("SBER_API_KEY", "")
    if not key or key == "mock":
        pytest.skip("set SBER_API_KEY to a real GigaChat key to run live tests")


@pytest.fixture()
def live_provider():
    _require_real_key()
    from app.providers.containers import provider
    from app.providers.mock import MOCK_API_KEY

    if isinstance(provider, type(None)) or getattr(provider, "api_key", "") == MOCK_API_KEY:
        pytest.skip("SBER_API_KEY is still 'mock'")
    return provider


@pytest_asyncio.fixture
async def sber_provider(real_provider_env, redis_client):
    """A real :class:`SberProvider` built from the variables the process started with.

    ``redis_client`` is not used directly: it patches the pool behind the global
    PERS API lock onto the test container, which is the only Redis a host-side
    run can reach. Without it the first request dies on the ``redis`` hostname.
    """
    from app.providers.mock import MOCK_API_KEY
    from app.providers.sber import SberProvider

    key = os.environ.get("SBER_API_KEY", "")
    scope = os.environ.get("SBER_API_SCOPE", "")
    if not key or key == MOCK_API_KEY:
        pytest.skip("set SBER_API_KEY to a real GigaChat key to run live tests")
    if scope not in ("PERS", "B2B", "CORP"):
        pytest.skip(f"SBER_API_SCOPE={scope!r} does not select the GigaChat client")

    provider = SberProvider(api_key=key, scope=scope)
    try:
        yield provider
    finally:
        await provider.close()


async def test_live_generate_against_gigachat(live_provider):
    """One real completion, verifying the harness schema round-trips."""
    import json

    from app.harness import HARNESS_STRUCTURED_SCHEMA
    from app.providers.models import Content, Message, ResponseFormat

    messages = [
        Message(role="system", content=[Content(text="Ты — инженер-сметчик.")]),
        Message(
            role="user",
            content=[Content(text="Прямоугольная пластина 200x100 мм, ст3 1 мм, 4 отверстия.")],
        ),
    ]
    response = await live_provider.generate(
        messages,
        ResponseFormat(
            type=ResponseFormat.TYPE_JSON_SCHEMA,
            schema=HARNESS_STRUCTURED_SCHEMA,
            strict=True,
        ),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert response is not None, "the live API returned an error"
    payload = json.loads(response.messages[0].content[0].text)
    assert payload["message"]
    assert set(payload) == {"chat_name", "message"}


async def test_live_gigachat_requests_a_tool_call(sber_provider):
    """One real completion that must come back as a function call, not text."""
    from app.providers.models import Content, FunctionDefinition, Message, ResponseFormat, Tool

    sber_provider.tools = [
        Tool(
            function=FunctionDefinition(
                name="get_weather",
                description="Прогноз погоды в городе",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "Название города"}},
                    "required": ["city"],
                },
            )
        )
    ]
    response = await sber_provider.generate(
        [Message(role="user", content=[Content(text="Какая погода в Москве? Вызови get_weather.")])],
        ResponseFormat(type=ResponseFormat.TYPE_TEXT),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert response is not None, "the live API returned an error"
    assert response.tool_calls, f"the model did not request the tool: {response.messages}"
    assert response.tool_calls[0].function.name == "get_weather"
    assert "Москва" in response.tool_calls[0].function.arguments


async def test_live_upload_and_reflect(live_provider):
    """One real file upload, checking the returned id is usable."""
    session_id = uuid.uuid4()
    sber_id = await live_provider.upload(
        "smoke.png",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        x_client_id=uuid.uuid4(),
        x_session_id=session_id,
        content_type="image/png",
    )
    assert sber_id


async def test_live_gigachat_completes_a_tool_round(sber_provider):
    """The whole round the pipeline depends on: model -> tool -> model.

    The first completion must ask for the tool, the result must go back as a
    GigaChat ``function_result`` part tied by ``tool_state_id``, and the second
    completion must answer in text. The offline suite checks the shape; only the
    live API can confirm it accepts that shape.
    """
    from app.agentic.loop import run_tool_loop
    from app.agentic.tools import ToolResult
    from app.providers.models import Content, FunctionDefinition, Message, ResponseFormat, Tool

    tool = Tool(
        function=FunctionDefinition(
            name="get_weather",
            description="Прогноз погоды в городе",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string", "description": "Название города"}},
                "required": ["city"],
            },
        )
    )
    rounds = 0
    client_id, session_id = uuid.uuid4(), uuid.uuid4()

    async def generate(messages):
        return await sber_provider.generate(
            messages,
            ResponseFormat(type=ResponseFormat.TYPE_TEXT),
            x_client_id=client_id,
            x_session_id=session_id,
            tools=[tool],
        )

    async def call_tool(call):
        nonlocal rounds
        rounds += 1
        return ToolResult(ok=True, note="В Москве +5 °C, ясно.")

    reply = await run_tool_loop(
        [
            Message(
                role="user", content=[Content(text="Узнай погоду в Москве через get_weather и ответь одной фразой.")]
            )
        ],
        generate=generate,
        call_tool=call_tool,
    )

    assert rounds == 1, f"the model did not complete a tool round: {reply!r}"
    assert reply
