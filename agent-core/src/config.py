"""
Environment configuration loader using Pydantic BaseSettings.

This module centralizes all environment configuration for the Alfred Agent Core.
It provides type safety, validation, and automatic loading from environment variables
and .env files. All settings are validated at startup to fail fast with clear errors.
"""

import json
import secrets
from typing import Annotated, Any, ClassVar, Dict, List, Optional, Set, Tuple

import structlog
from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
)
from pydantic.networks import HttpUrl, PostgresDsn
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = structlog.get_logger(__name__)


def parse_string_list(v: Any) -> List[str]:
    """
    Parse string lists from various input formats.

    Supports:
    - Native Python list (from code/tests)
    - JSON array string: '["header1", "header2"]'
    - Comma-separated string: 'header1,header2'
    - Empty string or None: returns empty list
    """
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # Handle JSON array format
        if s.startswith("["):
            return json.loads(s)
        # Parse comma-separated values
        return [item.strip() for item in s.split(",") if item.strip()]
    return v


def parse_cors(v: Any) -> List[str]:
    """
    Parse CORS origins from various input formats.

    Supports:
    - Native Python list (from code/tests)
    - JSON array string: '["https://api.example.com", "https://app.example.com"]'
    - Comma-separated string: 'https://api.example.com,https://app.example.com'
    - Empty string or None: returns empty list
    """
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # Handle JSON array format
        if s.startswith("["):
            return json.loads(s)
        # Parse comma-separated values
        return [origin.strip() for origin in s.split(",") if origin.strip()]
    return v


# Create reusable type annotation for CORS origins
# NoDecode prevents automatic JSON parsing, BeforeValidator applies our custom parser
CorsOrigins = Annotated[List[AnyHttpUrl], NoDecode, BeforeValidator(parse_cors)]


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Priority order for loading values:
    1. Environment variables (highest priority)
    2. .env file
    3. Default values defined here

    Required fields will cause startup failure if not provided.
    """

    # ===== Application Settings =====
    app_env: str = Field(
        default="development",
        description="Application environment (development/staging/production)",
    )

    app_name: str = Field(
        default="Alfred Agent Core",
        description="Application name for logging and identification",
    )

    app_version: str = Field(default="0.1.0", description="Application version")

    log_level: str = Field(
        default="INFO", description="Logging level (DEBUG/INFO/WARNING/ERROR)"
    )

    # ===== API Security =====
    api_key: str = Field(
        ...,  # Required field, no default
        description="API key for authenticating requests to this service",
        min_length=32,
    )

    cors_origins: CorsOrigins = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        description="List of allowed CORS origins (JSON array or comma-separated in env)",
    )

    # ===== Server Configuration =====
    host: str = Field(default="0.0.0.0", description="Host to bind the server to")

    port: int = Field(
        default=8080, description="Port to bind the server to", ge=1, le=65535
    )

    # ===== Anthropic Configuration =====
    anthropic_api_key: str = Field(
        ...,  # Required field
        description="Anthropic API key for Claude model access",
        pattern="^sk-ant-",  # Anthropic keys start with sk-ant-
    )

    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        description="Anthropic model to use for agent",
    )

    anthropic_max_tokens: int = Field(
        default=4096,
        description="Maximum tokens for Anthropic responses",
        ge=1,
        le=8192,
    )

    # ===== Database Configuration (Week 3) =====
    database_url: Optional[PostgresDsn] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL"),
        description="PostgreSQL connection URL (postgresql://user:pass@host:port/db)",
    )

    database_pool_size: int = Field(
        default=10, description="Database connection pool size", ge=1, le=100
    )

    database_pool_timeout: int = Field(
        default=30, description="Database connection pool timeout in seconds", ge=1
    )

    # ===== Notion OAuth Configuration (Week 2) =====
    notion_client_id: Optional[str] = Field(
        default=None, description="Notion OAuth app client ID"
    )

    notion_client_secret: Optional[str] = Field(
        default=None, description="Notion OAuth app client secret"
    )

    notion_redirect_uri: Optional[HttpUrl] = Field(
        default="http://localhost:8080/oauth/notion/callback",
        description="Notion OAuth callback URL",
    )

    notion_auth_url: str = Field(
        default="https://api.notion.com/v1/oauth/authorize",
        description="Notion OAuth authorization endpoint",
    )

    notion_token_url: str = Field(
        default="https://api.notion.com/v1/oauth/token",
        description="Notion OAuth token exchange endpoint",
    )

    # ===== OAuth Token Refresh Configuration (Issue #16) =====
    oauth_refresh_window_minutes: int = Field(
        default=5,
        description="Time window before token expiry to trigger proactive refresh",
        ge=1,
        le=60,
    )

    oauth_refresh_jitter_seconds: int = Field(
        default=60,
        description="Maximum jitter in seconds to prevent thundering herd during refresh",
        ge=0,
        le=300,
    )

    oauth_refresh_clock_skew_seconds: int = Field(
        default=60,
        description="Clock skew tolerance in seconds when checking token expiry",
        ge=0,
        le=300,
    )

    oauth_refresh_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for transient refresh failures",
        ge=1,
        le=10,
    )

    oauth_refresh_base_delay_ms: int = Field(
        default=100,
        description="Base delay in milliseconds for exponential backoff",
        ge=10,
        le=5000,
    )

    oauth_max_failure_count: int = Field(
        default=5,
        description="Maximum consecutive failures before requiring re-authentication",
        ge=1,
        le=20,
    )

    oauth_health_check_enabled: bool = Field(
        default=True, description="Enable OAuth health monitoring endpoints"
    )

    oauth_background_refresh_enabled: bool = Field(
        default=True,
        description="Enable background token refresh service (hybrid strategy)",
    )

    # ===== Security & Encryption =====
    fernet_key: Optional[str] = Field(
        default=None,
        description="Fernet encryption key for token storage (auto-generated if not provided)",
    )

    jwt_secret: Optional[str] = Field(
        default=None,
        description="JWT secret for device tokens (auto-generated if not provided)",
    )

    # ===== MCP Configuration =====
    mcp_timeout: int = Field(
        default=30000,
        description="MCP tool call timeout in milliseconds",
        ge=1000,
        le=120000,
    )

    mcp_notion_server_url: Optional[HttpUrl] = Field(
        default="http://localhost:3001", description="Notion MCP server URL"
    )

    mcp_github_server_url: Optional[HttpUrl] = Field(
        default="http://localhost:3002", description="GitHub MCP server URL"
    )

    # ===== Feature Flags =====
    FEATURE_NOTION_HOSTED_MCP: bool = Field(
        default=True,
        description="Enable Notion's hosted MCP service for authenticated users",
    )

    # ===== Cache Configuration =====
    cache_ttl_default: int = Field(
        default=3600, description="Default cache TTL in seconds (1 hour)", ge=0
    )

    cache_ttl_notion: int = Field(
        default=300,
        description="Notion-specific cache TTL in seconds (5 minutes)",
        ge=0,
    )

    cache_ttl_github: int = Field(
        default=900,
        description="GitHub-specific cache TTL in seconds (15 minutes)",
        ge=0,
    )

    cache_invalidation_cap_default: int = Field(
        default=100,
        description="Max entries invalidated per write operation (safety cap)",
        ge=1,
        le=1000,
    )

    # ===== Cache Configuration =====
    # Tools that should NEVER be cached (mutations, time-sensitive, auth operations)
    # All other tools will be cached with default TTL unless specified
    # NOTE: Only include unambiguous mutation/time operations. Avoid generic terms
    # that might appear in read-only tool names (e.g., "post", "pull", "open")
    CACHE_DENYLIST: ClassVar[Set[str]] = {
        # Time-sensitive operations (any server)
        "get_current_time",
        "convert_time",
        "get_time",
        "now",
        # Clear mutation operations - specific action verbs only
        "create",
        "update",
        "delete",
        "patch",  # HTTP PATCH is always mutation
        "merge",  # Git/PR merge
        "fork",  # Repository fork
        "push",  # Git push
        "commit",  # Git commit
        "rollback",
        "approve",  # PR/workflow approval
        "reject",  # PR/workflow rejection
        "archive",
        "unarchive",
        "assign",
        "unassign",
        "label",
        "unlabel",
        "tag",
        "untag",
        # Authentication/OAuth operations
        "auth",
        "oauth",
        "login",
        "logout",
        "token",
        "refresh",
        "connect",
        "disconnect",
        "authorize",
        "revoke",
        # Notification/webhook operations
        "notify",
        "send",
        "email",
        "webhook",
        "subscribe",
        "unsubscribe",
    }

    # Custom TTLs for specific tools (optional overrides)
    # Format: {("server", "tool_pattern"): ttl_seconds}
    CACHE_TTL_OVERRIDES: ClassVar[Dict[Tuple[str, str], int]] = {
        # Notion - longer TTL for stable content
        ("notion", "*page*"): 900,  # 15 min - pages change slowly
        ("notion", "*database*"): 900,  # 15 min - schema rarely changes
        ("notion", "*search*"): 300,  # 5 min - search results more dynamic
        # GitHub - varied TTLs based on content type
        ("github-*", "*file*"): 1800,  # 30 min - code rarely changes
        ("github-*", "*issue*"): 600,  # 10 min - issues update moderately
        ("github-*", "*pull*"): 600,  # 10 min - PRs update moderately
        ("github-*", "*search*"): 300,  # 5 min - search results change
        ("github-*", "*list*"): 300,  # 5 min - lists change frequently
        # Atlassian
        ("atlassian", "*page*"): 900,  # 15 min - Confluence pages
        ("atlassian", "*issue*"): 600,  # 10 min - JIRA issues
    }

    # ===== Thread Configuration (Issue #51) =====
    enable_threads: bool = Field(
        default=True,
        description="Enable thread support for conversation continuity",
    )

    thread_message_limit: int = Field(
        default=100,
        description="Maximum messages to fetch per thread",
        ge=1,
        le=500,
    )

    share_token_ttl_hours: int = Field(
        default=168,
        description="Share token validity in hours (default 1 week)",
        ge=1,
        le=720,  # Max 30 days
    )

    default_user_id: Optional[str] = Field(
        default="00000000-0000-0000-0000-000000000000",
        description="Default user UUID for MVP (should be None in production)",
        pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )

    # ===== Rate Limiting (Week 4) =====
    rate_limiting_enabled: bool = Field(
        default=True, description="Enable global rate limiting"
    )

    rate_limit_default_rpm: int = Field(
        default=60,
        description="Default requests per minute limit",
        ge=1,
        le=10000,
    )

    rate_limit_default_burst: int = Field(
        default=10,
        description="Default burst capacity (leaky bucket size)",
        ge=1,
        le=100,
    )

    rate_limit_max_buckets: int = Field(
        default=10000,
        description="Maximum rate limit buckets to prevent DoS",
        ge=100,
        le=100000,
    )

    rate_limit_cleanup_interval: int = Field(
        default=300,
        description="Bucket cleanup interval in seconds",
        ge=30,
        le=3600,
    )

    # JSON configuration for complex overrides
    rate_limit_route_overrides: str = Field(
        default="{}",
        description="Per-route rate limits as JSON (route -> {requests_per_minute, burst_capacity})",
    )

    rate_limit_key_overrides: str = Field(
        default="{}",
        description="Per-API-key rate limits as JSON (hashed_key -> {requests_per_minute, burst_capacity})",
    )

    # Multi-process deployment awareness
    web_concurrency: int = Field(
        default=1,
        description="Number of worker processes (affects effective rate limits)",
        ge=1,
        le=20,
    )

    # ===== Production Hardening (Issue #31) =====

    # Timeout Configuration
    request_timeout_seconds: int = Field(
        default=30,
        description="Max request processing time (excludes SSE)",
        ge=5,
        le=300,
    )

    keepalive_timeout_seconds: int = Field(
        default=5,
        description="HTTP keep-alive timeout",
        ge=1,
        le=60,
    )

    anthropic_timeout_seconds: int = Field(
        default=60,
        description="Anthropic API timeout",
        ge=10,
        le=300,
    )

    mcp_timeout_seconds: int = Field(
        default=30,
        description="MCP server timeout",
        ge=5,
        le=120,
    )

    # SSE streaming timeout (separate from request timeout)
    sse_max_duration_minutes: int = Field(
        default=15,
        description="Maximum SSE streaming duration in minutes",
        ge=5,
        le=60,
    )

    # Size Limits
    max_request_size_mb: int = Field(
        default=10,
        description="Max request body size in MB",
        ge=1,
        le=100,
    )

    max_chat_history_messages: int = Field(
        default=100,
        description="Max chat history length",
        ge=1,
        le=1000,
    )

    max_message_content_kb: int = Field(
        default=100,
        description="Max individual message size in KB",
        ge=1,
        le=1000,
    )

    max_tool_args_kb: int = Field(
        default=50,
        description="Max tool arguments size in KB",
        ge=1,
        le=500,
    )

    # CORS Configuration (Enhanced)
    cors_allow_credentials: bool = Field(
        default=True,
        description="Allow credentials in CORS requests",
    )

    cors_allow_methods: Annotated[
        List[str], BeforeValidator(parse_string_list)
    ] = Field(
        default=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        description="Allowed HTTP methods for CORS",
    )

    cors_allow_headers: Annotated[
        List[str], BeforeValidator(parse_string_list)
    ] = Field(
        default=["Content-Type", "Authorization", "X-Request-ID"],
        description="Allowed headers for CORS (never use * with credentials)",
    )

    cors_max_age: int = Field(
        default=86400,
        description="CORS preflight cache time in seconds",
        ge=0,
        le=86400,
    )

    # Security Headers
    security_headers_enabled: bool = Field(
        default=True,
        description="Enable security headers middleware",
    )

    hsts_enabled: bool = Field(
        default=False,
        description="Enable HSTS (only for HTTPS production)",
    )

    csp_enabled: bool = Field(
        default=True,
        description="Enable Content Security Policy",
    )

    csp_report_only: bool = Field(
        default=True,
        description="CSP in report-only mode (recommended for initial deployment)",
    )

    # Trusted Host Configuration (for proxy headers)
    trusted_hosts: Annotated[List[str], BeforeValidator(parse_string_list)] = Field(
        default=["*"],
        description="Trusted hosts (use specific domains in production)",
    )

    forwarded_allow_ips: Annotated[
        List[str], BeforeValidator(parse_string_list)
    ] = Field(
        default=["127.0.0.1", "::1"],
        description="IPs allowed to send proxy headers",
    )

    # Development/Production Feature Flags
    dev_mode_relaxed_security: bool = Field(
        default=True,
        description="Relax some security settings in development",
    )

    # ===== Validators =====

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: List[AnyHttpUrl], info) -> List[AnyHttpUrl]:
        """
        Validate CORS origins for security.

        - Prevents wildcard (*) in production
        - Ensures all origins are valid URLs (AnyHttpUrl handles this)
        """
        # Get app_env from the data being validated
        app_env = info.data.get("app_env", "development")

        # Check for wildcard in production
        if app_env == "production":
            for origin in origins:
                if str(origin) == "*":
                    raise ValueError("CORS wildcard (*) not allowed in production")

        return origins

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log level is valid."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v_upper

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        """Ensure app environment is valid."""
        valid_envs = ["development", "staging", "production", "test"]
        v_lower = v.lower()
        if v_lower not in valid_envs:
            raise ValueError(f"Invalid app_env: {v}. Must be one of {valid_envs}")
        return v_lower

    @field_validator("fernet_key", mode="before")
    @classmethod
    def generate_fernet_key_if_needed(cls, v: Optional[str]) -> str:
        """Generate Fernet key if not provided."""
        if v is None or v == "":
            # Generate a new Fernet key (URL-safe base64-encoded 32 bytes)
            from cryptography.fernet import Fernet

            key = Fernet.generate_key().decode()
            logger.warning(
                "Generated new Fernet key - save this in .env for persistence"
            )
            return key
        return v

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def generate_jwt_secret_if_needed(cls, v: Optional[str]) -> str:
        """Generate JWT secret if not provided."""
        if v is None or v == "":
            # Generate a secure random secret
            secret = secrets.token_urlsafe(32)
            logger.warning(
                "Generated new JWT secret - save this in .env for persistence"
            )
            return secret
        return v

    @field_validator("rate_limit_route_overrides", "rate_limit_key_overrides")
    @classmethod
    def validate_rate_limit_json_config(cls, v: str) -> str:
        """Validate JSON configuration for rate limit overrides."""
        if not v or v == "{}":
            return v

        try:
            config = json.loads(v)
            if not isinstance(config, dict):
                raise ValueError("Configuration must be a JSON object")

            # Validate structure
            for key, limits in config.items():
                if not isinstance(limits, dict):
                    raise ValueError(f"Override for '{key}' must be an object")

                # Validate required/optional fields
                if "requests_per_minute" in limits:
                    rpm = limits["requests_per_minute"]
                    if not isinstance(rpm, int) or rpm < 1 or rpm > 10000:
                        raise ValueError(
                            f"Invalid requests_per_minute for '{key}': {rpm}"
                        )

                if "burst_capacity" in limits:
                    burst = limits["burst_capacity"]
                    if not isinstance(burst, int) or burst < 1 or burst > 100:
                        raise ValueError(f"Invalid burst_capacity for '{key}': {burst}")

                if "enabled" in limits:
                    enabled = limits["enabled"]
                    if not isinstance(enabled, bool):
                        raise ValueError(f"Invalid enabled flag for '{key}': {enabled}")

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON configuration: {e}") from e
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid rate limit configuration: {e}") from e

        return v

    @field_validator("web_concurrency")
    @classmethod
    def warn_multiprocess_rate_limiting(cls, v: int, info) -> int:
        """Warn about rate limiting implications with multiple workers."""
        # Get rate_limiting_enabled from the data being validated
        rate_limiting_enabled = info.data.get("rate_limiting_enabled", True)

        if v > 1 and rate_limiting_enabled:
            import warnings

            warnings.warn(
                f"WEB_CONCURRENCY={v} with memory-based rate limiting. "
                "Effective limits will be multiplied by worker count. "
                "Consider Redis backend for multi-process deployment.",
                UserWarning,
                stacklevel=2,
            )
        return v

    # ===== Pydantic Config =====

    model_config = SettingsConfigDict(
        env_file=".env",  # Load from .env file
        env_file_encoding="utf-8",
        case_sensitive=False,  # Accept API_KEY or api_key
        extra="ignore",  # Ignore extra env variables
        # Add field descriptions to schema
        json_schema_extra={
            "examples": [
                {
                    "api_key": "your-secure-api-key-min-32-chars-long",
                    "anthropic_api_key": "sk-ant-api03-...",
                    "database_url": "postgresql://user:password@localhost:5432/agent_core",
                    "cors_origins": ["http://localhost:3000", "http://localhost:8080"],
                }
            ]
        },
    )

    def log_config(self) -> None:
        """Log configuration (with secrets masked)."""
        config_dict = self.model_dump()

        # List of sensitive fields to mask
        sensitive_fields = [
            "api_key",
            "anthropic_api_key",
            "notion_client_secret",
            "fernet_key",
            "jwt_secret",
            "database_url",
        ]

        # Mask sensitive values
        for field in sensitive_fields:
            if field in config_dict and config_dict[field]:
                # Show first 4 chars for debugging, mask the rest
                value = str(config_dict[field])
                if len(value) > 8:
                    config_dict[field] = f"{value[:4]}...{value[-4:]}"
                else:
                    config_dict[field] = "***"

        logger.info("Configuration loaded", **config_dict)

    def validate_required_for_production(self) -> None:
        """Additional validation for production environment."""
        if self.app_env == "production":
            errors = []

            # Check required production settings
            if not self.database_url:
                errors.append("DATABASE_URL is required in production")

            if not self.notion_client_id or not self.notion_client_secret:
                errors.append("Notion OAuth credentials required in production")

            if self.log_level == "DEBUG":
                logger.warning(
                    "DEBUG log level in production - consider using INFO or higher"
                )

            # CORS wildcard check is now handled in the field validator
            # but we can add additional checks here if needed

            if errors:
                raise ValueError(
                    f"Production configuration errors: {'; '.join(errors)}"
                )


# ===== Global Settings Instance =====

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Get the global settings instance (singleton pattern).

    This ensures we only load and validate settings once during application startup.
    Use this function as a FastAPI dependency for injecting settings.

    Example:
        @app.get("/")
        async def root(settings: Settings = Depends(get_settings)):
            return {"app": settings.app_name}
    """
    global _settings
    if _settings is None:
        try:
            _settings = Settings()
            _settings.log_config()
            _settings.validate_required_for_production()
            logger.info(
                "Settings loaded successfully",
                app_env=_settings.app_env,
                app_version=_settings.app_version,
            )
        except ValidationError as e:
            logger.error("Failed to load settings", errors=e.errors())
            raise
        except Exception as e:
            logger.error("Unexpected error loading settings", error=str(e))
            raise

    return _settings


def reset_settings() -> None:
    """Reset settings (useful for testing)."""
    global _settings
    _settings = None
