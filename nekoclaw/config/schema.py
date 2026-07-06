# AutoCython No Compile
"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    reply_to_message: bool = False  # If true, bot replies quote the original message


class QQConfig(Base):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(
        default_factory=list
    )  # Allowed user openids (empty = public access)


class NekoChatConfig(Base):
    """NekoChat web UI channel configuration."""

    enabled: bool = True  # It is the default enabled channel
    host: str = "127.0.0.1"
    port: int = 8899
    allow_from: list[str] = Field(default_factory=lambda: ["*"])  # "*" = allow all


class ChannelsConfig(Base):
    """Configuration for chat channels."""
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    nekochat: NekoChatConfig = Field(default_factory=NekoChatConfig)


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = (Path.home() / ".nekoclaw/workspace").as_posix()
    # Qualified model id in ``providerName/modelId`` form (e.g.
    # ``default/gpt-5.4`` or ``openrouter/openai/gpt-5.5``). The part before the
    # first ``/`` names the provider in ``providers.openai``; the remainder is
    # the bare model id sent to the API. An unqualified value (no ``/`` matching
    # a known provider name) falls back to the first configured provider — see
    # :meth:`ProvidersConfig.resolve`.
    model: str = "default/gpt-5.4"
    max_tokens: int = 32768
    temperature: float = 0.1
    max_tool_iterations: int = 40
    memory_window: int = 100
    reasoning_effort: str | None = "medium"  # low / medium / high — enables LLM thinking mode


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


# Curated short-list of recommended model ids per upstream base URL.
# Extend this map to teach ``ProviderConfig.infer_models`` about new providers.
_RECOMMENDED_MODELS_BY_HOST: dict[str, list[str]] = {
    "openrouter.ai": [
        "openai/gpt-5.5",
        "anthropic/claude-sonnet-4.6",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3-flash-preview",
        "z-ai/glm-5.1",
        "moonshotai/kimi-k2.6",
    ],
}


class ModelConfig(Base):
    """A single model offered by a provider, with per-model capabilities."""

    id: str  # Bare model id sent to the API (e.g. "gpt-5.5", "openai/gpt-5.5")
    # Whether the model can accept image (vision) inputs. When ``false``,
    # image parts are stripped from outgoing requests and replaced with a
    # ``[image]`` text placeholder so non-vision models don't error out.
    image_input: bool = True
    # Whether to send the assistant's prior reasoning/thinking content back to
    # the model on subsequent turns (as ``reasoning_content``). Needed by some
    # reasoning models — e.g. DeepSeek V4 and Kimi — that expect their own
    # chain-of-thought echoed back. Off by default to avoid leaking thinking to
    # providers that reject the field.
    include_reasoning: bool = False


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    # Models offered by this provider, surfaced by the UI as quick-pick
    # suggestions and carrying per-model capability flags. If empty, it is
    # auto-populated from ``api_base`` via :meth:`infer_models` — see the
    # ``_apply_infer_models`` validator below. Any non-empty list supplied in
    # JSON (e.g. customized by ``prompt_configs``) is preserved so the user's
    # picks round-trip through reloads.
    models: list[ModelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _apply_infer_models(self) -> "ProviderConfig":
        """Seed ``models`` from ``api_base`` when it is empty."""
        if not self.models:
            self.models = self.infer_models(self.api_base)
        return self

    @property
    def model_ids(self) -> list[str]:
        """Return the bare model ids offered by this provider."""
        return [m.id for m in self.models]

    def get_model(self, model_id: str) -> ModelConfig | None:
        """Return the :class:`ModelConfig` for ``model_id`` if listed."""
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    @staticmethod
    def infer_models(api_base: str | None) -> list[ModelConfig]:
        """Return the recommended model list for the given base URL.

        The match is a case-insensitive substring check against
        :data:`_RECOMMENDED_MODELS_BY_HOST` so subpath/regional URLs still
        resolve to the right preset (e.g. ``https://openrouter.ai/api/v1``).
        Returns an empty list when no preset is known for the URL.
        """
        if not api_base:
            return []
        haystack = api_base.lower()
        for host, models in _RECOMMENDED_MODELS_BY_HOST.items():
            if host in haystack:
                return [ModelConfig(id=m) for m in models]
        return []


class ResolvedModel(NamedTuple):
    """The provider/model pair a qualified model string resolves to."""

    provider_name: str
    provider: ProviderConfig
    model_id: str
    model: ModelConfig


def split_qualified_model(qualified: str) -> tuple[str | None, str]:
    """Split ``providerName/modelId`` on the first ``/``.

    Returns ``(provider_name, model_id)``. When there is no ``/`` the provider
    name is ``None`` and the whole string is treated as the model id.
    """
    name, sep, model_id = (qualified or "").partition("/")
    if not sep:
        return None, qualified or ""
    return name, model_id


def resolve_model_tag(qualified: str) -> str:
    """Return a ``providerName:modelId`` tag for a qualified model string.

    Used to stamp persisted assistant deltas with the model that produced them.
    Resolution prefers the global config (so an unqualified id picks up the
    active provider name); it falls back to a best-effort split when the config
    is unavailable.
    """
    try:
        from nekoclaw.config.manager import get_global_config

        resolved = get_global_config().providers.resolve(qualified)
        return f"{resolved.provider_name}:{resolved.model_id}"
    except Exception:
        name, model_id = split_qualified_model(qualified)
        return f"{name}:{model_id}" if name else (qualified or "")


class ProvidersConfig(Base):
    """Configuration for LLM providers.

    ``openai`` is a mapping of unique provider names to OpenAI-compatible
    provider configs, so several gateways/endpoints can coexist. The active
    model is chosen via a qualified ``providerName/modelId`` string (see
    :attr:`AgentDefaults.model` and :meth:`resolve`).
    """

    openai: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {"default": ProviderConfig()}
    )

    def resolve(self, qualified: str) -> ResolvedModel:
        """Resolve a qualified ``providerName/modelId`` string.

        Resolution rules:
        - If the part before the first ``/`` matches a configured provider
          name, that provider is used and the remainder is the model id.
        - Otherwise the first configured provider is used and the entire
          string is treated as the model id (handles legacy/unqualified ids
          such as ``openai/gpt-5.5`` where no provider is named ``openai``).

        The returned :class:`ResolvedModel` always carries a usable
        :class:`ModelConfig`: the one listed by the provider when present, or a
        default (both flags ``False``) synthesized from the bare model id.
        """
        providers = self.openai or {"default": ProviderConfig()}
        name, model_id = split_qualified_model(qualified)

        if name is not None and name in providers:
            provider_name = name
            provider = providers[name]
        else:
            provider_name = next(iter(providers))
            provider = providers[provider_name]
            model_id = qualified or model_id

        model = provider.get_model(model_id) or ModelConfig(id=model_id)
        return ResolvedModel(provider_name, provider, model_id, model)


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class GatewayConfig(Base):
    """Gateway/server configuration."""

    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchEnginesConfig(Base):
    """Toggles for individual web search engines."""

    baidu: bool = True
    google: bool = True
    bing: bool = True
    duckduckgo: bool = True


class WebSearchConfig(Base):
    """Web search tool configuration."""

    max_results: int = 20
    engines: WebSearchEnginesConfig = Field(default_factory=WebSearchEnginesConfig)


class WebToolsConfig(Base):
    """Web tools configuration."""

    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    headless: bool = False
    chrome_executable_path: str | None = "./resources/chrome/chrome-win64/chrome.exe"
    user_data_dir: str = (Path.home() / ".nekoclaw/browser_data").as_posix()
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 30
    path_append: str = ""
    profile_files: list[str] = Field(
        default_factory=lambda: [".profile", (Path.home() / ".nekoclaw" / ".profile").as_posix()]
    )
    profile_commands: list[str] = Field(default_factory=list)


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Config(BaseSettings):
    """Root configuration for nekoclaw."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()


    model_config = ConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")
