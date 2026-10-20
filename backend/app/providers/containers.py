import os

from app.providers.client import AIClient
from app.providers.mock import MOCK_API_KEY, MockProvider
from app.providers.models import Tool
from app.providers.openai import OpenAIProvider
from app.providers.sber import SberProvider

api_key = os.environ["SBER_API_KEY"]
scope = os.environ["SBER_API_SCOPE"]

#: The provider's default tool set: empty on purpose. Every turn of the chat and
#: of the programme pipeline names the set it offers — ``INTAKE_TOOLS`` at the
#: intake, ``stage_tools(stage)`` inside a stage — because a stage that saw the
#: intake tools could start a second run mid-document. A union here would also
#: advertise four same-named ``save_program_draft`` specs, one per stage block.
TOOLS: list[Tool] = []

# Selection is driven by ``SBER_API_SCOPE``: ``openai`` uses the OpenAI-compatible
# client, ``mock`` the offline stand-in, anything else the real GigaChat client.
# ``SBER_API_KEY=mock`` still selects the mock regardless of scope, and it is
# checked first: the e2e and test stacks set the key to guarantee that no call
# leaves the process, so a leftover ``SBER_API_SCOPE=openai`` must not defeat them.
provider: AIClient
if api_key == MOCK_API_KEY or scope == "mock":
    provider = MockProvider(api_key=api_key, scope=scope, tools=TOOLS)
elif scope == "openai":
    provider = OpenAIProvider(api_key=api_key, scope=scope, tools=TOOLS)
else:
    provider = SberProvider(api_key=api_key, scope=scope, tools=TOOLS)
