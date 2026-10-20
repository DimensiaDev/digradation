import os

from app.agentic.tools import AGENT_TOOLS
from app.providers.client import AIClient
from app.providers.mock import MOCK_API_KEY, MockProvider
from app.providers.models import Tool
from app.providers.openai import OpenAIProvider
from app.providers.sber import SberProvider

api_key = os.environ["SBER_API_KEY"]
scope = os.environ["SBER_API_SCOPE"]

#: Tools offered to the model. The intake request and the stage tasks pass their
#: own subsets explicitly; this list is the union the provider advertises when a
#: caller does not narrow it.
TOOLS: list[Tool] = AGENT_TOOLS

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
