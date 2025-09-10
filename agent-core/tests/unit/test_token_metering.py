"""
Unit tests for the token metering service.

Tests cover:
- Idempotent token tracking
- Daily rollup updates
- Budget checking with warning levels
- Cache hit tracking with zero tokens
"""

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TokenUsage, TokenUsageRollupDaily, User, UserTokenBudget
from src.services.token_metering import TokenMeteringService


@pytest.fixture
def token_metering():
    """Create a token metering service instance."""
    return TokenMeteringService()


@pytest.fixture
async def test_user(db_session: AsyncSession):
    """Create a test user for token tracking."""
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    return user


@pytest.fixture
def test_request_id():
    """Generate a test request ID."""
    return uuid.uuid4()


class TestTokenMetering:
    """Test suite for token metering service."""

    async def test_track_request_tokens_basic(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
        test_request_id: uuid.UUID,
    ):
        """Test basic token tracking with idempotency."""
        # Track initial request
        await token_metering.track_request_tokens(
            db_session,
            request_id=test_request_id,
            user_id=test_user.id,
            input_tokens=100,
            output_tokens=50,
            model_name="claude-3-opus",
            provider="anthropic",
        )
        # Don't commit here - let the test transaction handle it

        # Verify token usage was recorded
        stmt = select(TokenUsage).where(TokenUsage.request_id == test_request_id)
        result = await db_session.execute(stmt)
        usage = result.scalar_one()

        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.model_name == "claude-3-opus"
        assert usage.provider == "anthropic"
        assert usage.status == "ok"
        assert not usage.cache_hit

    async def test_track_request_tokens_idempotent(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
        test_request_id: uuid.UUID,
    ):
        """Test idempotent token tracking with retries."""
        # Track initial request with partial tokens
        await token_metering.track_request_tokens(
            db_session,
            request_id=test_request_id,
            user_id=test_user.id,
            input_tokens=50,
            output_tokens=25,
        )

        # Retry with higher token counts (simulating retry)
        await token_metering.track_request_tokens(
            db_session,
            request_id=test_request_id,
            user_id=test_user.id,
            input_tokens=100,  # Higher count
            output_tokens=50,  # Higher count
        )

        # Verify maximum tokens were kept
        stmt = select(TokenUsage).where(TokenUsage.request_id == test_request_id)
        result = await db_session.execute(stmt)
        usage = result.scalar_one()

        assert usage.input_tokens == 100  # Maximum was kept
        assert usage.output_tokens == 50  # Maximum was kept

    async def test_cache_hit_zero_tokens(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
        test_request_id: uuid.UUID,
    ):
        """Test that cache hits record zero tokens."""
        # Track cache hit
        await token_metering.track_request_tokens(
            db_session,
            request_id=test_request_id,
            user_id=test_user.id,
            input_tokens=999,  # Should be overridden to 0
            output_tokens=999,  # Should be overridden to 0
            cache_hit=True,
        )

        # Verify zero tokens for cache hit
        stmt = select(TokenUsage).where(TokenUsage.request_id == test_request_id)
        result = await db_session.execute(stmt)
        usage = result.scalar_one()

        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_hit
        assert usage.status == "cache"

    async def test_daily_rollup_update(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
    ):
        """Test daily rollup table updates."""
        # Track multiple requests
        for i in range(3):
            await token_metering.track_request_tokens(
                db_session,
                request_id=uuid.uuid4(),
                user_id=test_user.id,
                workspace_id="test-workspace",
                input_tokens=100,
                output_tokens=50,
                cache_hit=(i == 2),  # Last one is a cache hit
            )

        # Check rollup was updated
        today = date.today()
        stmt = select(TokenUsageRollupDaily).where(
            TokenUsageRollupDaily.user_id == test_user.id,
            TokenUsageRollupDaily.workspace_id == "test-workspace",
            TokenUsageRollupDaily.day == today,
        )
        result = await db_session.execute(stmt)
        rollup = result.scalar_one()

        assert rollup.input_tokens == 200  # 2 real requests * 100
        assert rollup.output_tokens == 100  # 2 real requests * 50
        assert rollup.request_count == 3
        assert rollup.cache_hits == 1
        assert rollup.error_count == 0

    async def test_get_user_usage(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
    ):
        """Test retrieving user usage from rollup."""
        # Track some usage
        await token_metering.track_request_tokens(
            db_session,
            request_id=uuid.uuid4(),
            user_id=test_user.id,
            input_tokens=500,
            output_tokens=250,
        )

        # Get usage
        usage = await token_metering.get_user_usage(
            db_session,
            user_id=test_user.id,
        )

        assert usage["input_tokens"] == 500
        assert usage["output_tokens"] == 250
        assert usage["request_count"] == 1
        assert usage["cache_hits"] == 0

    async def test_budget_check_warning_levels(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
    ):
        """Test budget checking with warning levels."""
        # Create custom budget with low limit
        budget = UserTokenBudget(
            user_id=test_user.id,
            workspace_id=None,
            daily_limit=1000,  # Low limit for testing
            monthly_limit=30000,
            warning_threshold_percent=80,
            soft_block=True,
        )
        db_session.add(budget)

        # Track usage at different levels
        test_cases = [
            (700, False, None),  # 70% - no warning
            (800, True, "warning"),  # 80% - warning
            (950, True, "critical"),  # 95% - critical
            (1100, True, "over"),  # 110% - over
        ]

        for total_tokens, expected_over, expected_level in test_cases:
            # Clear previous usage
            stmt = select(TokenUsageRollupDaily).where(
                TokenUsageRollupDaily.user_id == test_user.id
            )
            result = await db_session.execute(stmt)
            for rollup in result.scalars():
                await db_session.delete(rollup)

            # Track new usage
            await token_metering.track_request_tokens(
                db_session,
                request_id=uuid.uuid4(),
                user_id=test_user.id,
                input_tokens=total_tokens,
                output_tokens=0,
            )

            # Check budget
            (
                over_threshold,
                percent_used,
                warning_level,
            ) = await token_metering.check_budget(
                db_session,
                user_id=test_user.id,
            )

            assert over_threshold == expected_over
            assert warning_level == expected_level
            assert percent_used == int((total_tokens / 1000) * 100)

    async def test_device_usage_tracking(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
    ):
        """Test device-specific usage tracking - skipped as it requires device sessions."""
        # Skip this test for now as it requires device session setup
        pytest.skip("Requires device session entity setup")

    async def test_thread_usage_tracking(
        self,
        db_session: AsyncSession,
        token_metering: TokenMeteringService,
        test_user: User,
    ):
        """Test thread-specific usage tracking - skipped as it requires thread setup."""
        # Skip this test for now as it requires thread entity setup
        pytest.skip("Requires thread entity setup")
