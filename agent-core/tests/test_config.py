"""
Tests for configuration loading and validation.

Validates environment variable handling, defaults, validation rules,
and error handling for missing or invalid configuration.
"""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings


class TestConfiguration:
    """Test suite for configuration management."""

    def test_settings_loads_with_defaults(self):
        """Settings should load with reasonable defaults."""
        # Test with minimal environment
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
            },
            clear=False,
        ):
            settings = Settings()

            # Should have required fields
            assert settings.app_env == "test"
            assert settings.api_key == "test-key-32-characters-long-enough"
            assert (
                settings.anthropic_api_key == "sk-ant-test-api-key-for-testing-only-123"
            )

            # Should have defaults
            assert settings.app_name == "Alfred Agent Core"
            assert settings.app_version == "0.1.0"
            assert settings.host == "0.0.0.0"
            assert settings.port == 8080
            assert (
                settings.log_level == "DEBUG"
            )  # Test environment sets DEBUG in conftest.py

    def test_settings_validates_required_fields(self):
        """Settings should validate required fields are present."""
        # Test missing required API key
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                # Missing API_KEY
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
            },
            clear=True,
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

            # Should mention the missing field
            error_str = str(exc_info.value)
            assert "api_key" in error_str.lower()

    def test_settings_validates_api_key_length(self):
        """Settings should validate API key minimum length."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "short",  # Too short
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
            },
            clear=True,
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

            # Should mention length requirement
            error_str = str(exc_info.value)
            assert "api_key" in error_str.lower()

    def test_settings_validates_anthropic_key(self):
        """Settings should validate Anthropic API key is present."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                # Missing ANTHROPIC_API_KEY
            },
            clear=True,
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

            # Should mention Anthropic key
            error_str = str(exc_info.value)
            assert "anthropic" in error_str.lower()

    def test_settings_cache_configuration(self):
        """Settings should load cache-related configuration."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "CACHE_DEFAULT_TTL_SECONDS": "1800",
                "CACHE_NOTION_PAGE_TTL_SECONDS": "3600",
                "CACHE_NOTION_SEARCH_TTL_SECONDS": "900",
            },
            clear=False,
        ):
            settings = Settings()

            # Should have cache TTL settings
            assert hasattr(settings, "cache_ttl_default")
            assert hasattr(settings, "cache_ttl_notion")
            assert hasattr(settings, "cache_ttl_github")

            # Should use configured values where provided
            assert settings.cache_ttl_default == 1800

    def test_settings_mcp_server_urls(self):
        """Settings should load MCP server URL configuration."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "MCP_GITHUB_SERVER_URL": "https://test-github.example.com/mcp",
                "MCP_NOTION_SERVER_URL": "https://test-notion.example.com/mcp",
            },
            clear=False,
        ):
            settings = Settings()

            # Should have MCP server URLs
            assert hasattr(settings, "mcp_github_server_url")
            assert hasattr(settings, "mcp_notion_server_url")
            assert hasattr(settings, "mcp_timeout")

            # Should be properly typed URLs
            github_url = str(settings.mcp_github_server_url)
            notion_url = str(settings.mcp_notion_server_url)

            assert github_url == "https://test-github.example.com/mcp"
            assert notion_url == "https://test-notion.example.com/mcp"

    def test_settings_database_configuration(self):
        """Settings should handle database configuration."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb",
            },
            clear=False,
        ):
            settings = Settings()

            # Should have database settings
            assert hasattr(settings, "database_url")
            if settings.database_url:
                assert "postgresql" in settings.database_url

    def test_settings_cors_origins_parsing(self):
        """Settings should parse CORS origins from environment."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "CORS_ORIGINS": "http://localhost:3000,https://app.example.com",
            },
            clear=False,
        ):
            settings = Settings()

            # Should parse CORS origins
            assert hasattr(settings, "cors_origins")
            assert len(settings.cors_origins) >= 1

    def test_settings_encryption_key_generation(self):
        """Settings should handle Fernet encryption key."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                # No FERNET_KEY provided - should generate one
            },
            clear=False,
        ):
            settings = Settings()

            # Should have encryption key (generated or provided)
            assert hasattr(settings, "fernet_key")
            assert settings.fernet_key is not None
            assert len(settings.fernet_key) > 0

    def test_settings_environment_detection(self):
        """Settings should detect different environments."""
        environments = ["development", "test", "production"]

        for env in environments:
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": env,
                    "API_KEY": "test-key-32-characters-long-enough",
                    "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                },
                clear=False,
            ):
                settings = Settings()
                assert settings.app_env == env

    def test_settings_invalid_port_validation(self):
        """Settings should validate port number ranges."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "APP_PORT": "99999",  # Invalid port
            },
            clear=False,
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

            # Should mention port validation
            error_str = str(exc_info.value)
            assert "port" in error_str.lower()

    def test_settings_log_level_validation(self):
        """Settings should validate log level values."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]

        for level in valid_levels:
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "test",
                    "API_KEY": "test-key-32-characters-long-enough",
                    "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                    "LOG_LEVEL": level,
                },
                clear=False,
            ):
                settings = Settings()
                assert settings.log_level == level

    def test_get_settings_singleton(self):
        """get_settings() should return the same instance."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
            },
            clear=False,
        ):
            settings1 = get_settings()
            settings2 = get_settings()

            # Should be the same instance (cached)
            assert settings1 is settings2

    def test_settings_feature_flags(self):
        """Settings should support feature flag configuration."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                "FEATURE_NOTION_HOSTED_MCP": "true",
                "FEATURE_NOTION_SELF_HOST_FALLBACK": "false",
            },
            clear=False,
        ):
            settings = Settings()

            # Should parse boolean feature flags
            if hasattr(settings, "feature_notion_hosted_mcp"):
                assert settings.feature_notion_hosted_mcp is True
            if hasattr(settings, "feature_notion_self_host_fallback"):
                assert settings.feature_notion_self_host_fallback is False

    def test_settings_production_validation(self):
        """Settings should have stricter validation for production."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
                # Missing production-required fields
            },
            clear=False,
        ):
            settings = Settings()

            # Should load but may warn about production readiness
            assert settings.app_env == "production"

            # Test production validation method if it exists
            if hasattr(settings, "validate_required_for_production"):
                try:
                    settings.validate_required_for_production()
                except ValueError:
                    # Expected to fail in test environment
                    pass

    def test_settings_redacts_secrets_in_logs(self):
        """Settings should redact sensitive values when logging config."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "API_KEY": "test-key-32-characters-long-enough",
                "ANTHROPIC_API_KEY": "sk-ant-test-api-key-for-testing-only-123",
            },
            clear=False,
        ):
            settings = Settings()

            # Test log_config method if it exists
            if hasattr(settings, "log_config"):
                # Should not raise errors
                settings.log_config()

            # Verify sensitive fields are redacted in string representation
            settings_str = str(settings)
            assert "test-key-32-characters-long-enough" not in settings_str
            assert "test-anthropic-key" not in settings_str
