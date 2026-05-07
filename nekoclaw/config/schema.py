# AutoCython No Compile
"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Literal

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
    template_locale: Literal["en", "cn"] = Field(
        default="en",
        description="Locale for bundled workspace templates synced on startup (en or cn).",
    )
    model: str = "gpt-5.4"
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


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    # Curated list of model ids surfaced by the UI as quick-pick suggestions.
    # Always auto-derived from ``api_base`` via :meth:`infer_models` — see the
    # ``_apply_infer_models`` validator below — so any value supplied in JSON
    # or via the manager API is overwritten on validation.
    recommended_models: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _apply_infer_models(self) -> "ProviderConfig":
        """Re-derive ``recommended_models`` from ``api_base`` on every load."""
        self.recommended_models = self.infer_models(self.api_base)
        return self

    @staticmethod
    def infer_models(api_base: str | None) -> list[str]:
        """Return the recommended model id list for the given base URL.

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
                return list(models)
        return []


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    openai: ProviderConfig = Field(default_factory=ProviderConfig)


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchEnginesConfig(Base):
    """Toggles for individual web search engines."""

    baidu: bool = True
    google: bool = True
    bing: bool = True
    duckduckgo: bool = True


class WebSearchConfig(Base):
    """Web search tool configuration."""

    max_results: int = 10
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
