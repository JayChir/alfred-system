"""
Token metering service for usage tracking and budget management.

This service provides:
- Request-level token tracking with idempotency
- Daily rollup maintenance for O(1) reads
- Budget checking with warning levels
- Device and thread usage aggregation
"""

from datetime import date
from typing import Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import Integer, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TokenUsage, TokenUsageRollupDaily, UserTokenBudget
from src.utils.logging import get_logger

logger = get_logger(__name__)


class TokenMeteringService:
    """
    Service for tracking token usage and managing budgets.

    Features:
    - Idempotent token tracking via unique request_id
    - Automatic daily rollup updates for fast queries
    - Budget checking with warning levels
    - Cache hit tracking with zero tokens
    """

    def __init__(self):
        """Initialize the token metering service."""
        pass

    async def track_request_tokens(
        self,
        db: AsyncSession,
        *,
        request_id: UUID,
        user_id: UUID,
        workspace_id: Optional[str] = None,
        device_session_id: Optional[UUID] = None,
        thread_id: Optional[UUID] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        cache_hit: bool = False,
        tool_calls_count: int = 0,
        status: str = "ok",
    ) -> None:
        """
        Track token usage for a request with idempotency.

        Uses ON CONFLICT to handle retries gracefully, taking the maximum
        token values to avoid undercounting on partial failures.

        Args:
            db: Database session
            request_id: Unique request identifier (for idempotency)
            user_id: User who made the request
            workspace_id: Optional workspace context
            device_session_id: Optional device session
            thread_id: Optional thread reference
            input_tokens: Input tokens consumed (0 for cache hits)
            output_tokens: Output tokens generated (0 for cache hits)
            model_name: Model used (e.g., claude-3-opus)
            provider: Provider name (e.g., anthropic)
            cache_hit: Whether this was served from cache
            tool_calls_count: Number of tool calls made
            status: Request status (ok, error, cache)
        """
        try:
            # For cache hits, ensure tokens are zero
            if cache_hit:
                input_tokens = 0
                output_tokens = 0
                if status == "ok":
                    status = "cache"

            # Insert or update token usage (idempotent via request_id)
            stmt = insert(TokenUsage).values(
                request_id=request_id,
                user_id=user_id,
                workspace_id=workspace_id,
                device_session_id=device_session_id,
                thread_id=thread_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_name=model_name,
                provider=provider,
                tool_calls_count=tool_calls_count,
                cache_hit=cache_hit,
                status=status,
            )

            # On conflict, take maximum tokens (handles retries)
            stmt = stmt.on_conflict_do_update(
                index_elements=["request_id"],
                set_={
                    "input_tokens": func.greatest(
                        TokenUsage.input_tokens, stmt.excluded.input_tokens
                    ),
                    "output_tokens": func.greatest(
                        TokenUsage.output_tokens, stmt.excluded.output_tokens
                    ),
                    "status": stmt.excluded.status,
                    "cache_hit": TokenUsage.cache_hit | stmt.excluded.cache_hit,
                    "tool_calls_count": func.greatest(
                        TokenUsage.tool_calls_count, stmt.excluded.tool_calls_count
                    ),
                },
            )

            await db.execute(stmt)

            # Update daily rollup (atomic upsert)
            await self._update_daily_rollup(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit=cache_hit,
                is_error=(status == "error"),
            )

            logger.debug(
                "Token usage tracked",
                request_id=str(request_id),
                user_id=str(user_id),
                workspace_id=workspace_id,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                cache_hit=cache_hit,
                status=status,
            )

        except Exception as e:
            # Log but don't fail the request
            logger.error(
                "Failed to track token usage",
                request_id=str(request_id),
                error=str(e),
            )

    async def _update_daily_rollup(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: Optional[str],
        input_tokens: int,
        output_tokens: int,
        cache_hit: bool,
        is_error: bool,
    ) -> None:
        """
        Update daily rollup table with atomic upsert.

        This maintains pre-aggregated totals for O(1) budget checks.
        """
        today = date.today()  # UTC date

        # Prepare values for upsert
        values = {
            "user_id": user_id,
            "workspace_id": workspace_id or "",  # Use empty string for NULL
            "day": today,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "request_count": 1,
            "cache_hits": 1 if cache_hit else 0,
            "error_count": 1 if is_error else 0,
        }

        # Upsert with atomic addition
        stmt = insert(TokenUsageRollupDaily).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "workspace_id", "day"],
            set_={
                "input_tokens": TokenUsageRollupDaily.input_tokens
                + stmt.excluded.input_tokens,
                "output_tokens": TokenUsageRollupDaily.output_tokens
                + stmt.excluded.output_tokens,
                "request_count": TokenUsageRollupDaily.request_count + 1,
                "cache_hits": TokenUsageRollupDaily.cache_hits
                + stmt.excluded.cache_hits,
                "error_count": TokenUsageRollupDaily.error_count
                + stmt.excluded.error_count,
                "updated_at": func.now(),
            },
        )

        await db.execute(stmt)

    async def get_device_usage(
        self,
        db: AsyncSession,
        device_session_id: UUID,
        day: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Get token usage for a device session.

        Args:
            db: Database session
            device_session_id: Device session to query
            day: Specific day (None for today)

        Returns:
            Dictionary with input_tokens, output_tokens, request_count
        """
        if day is None:
            day = date.today()

        # Query raw usage table for device-specific metrics
        stmt = select(
            func.sum(TokenUsage.input_tokens).label("input_tokens"),
            func.sum(TokenUsage.output_tokens).label("output_tokens"),
            func.count(TokenUsage.id).label("request_count"),
            func.sum(func.cast(TokenUsage.cache_hit, Integer)).label("cache_hits"),
        ).where(
            and_(
                TokenUsage.device_session_id == device_session_id,
                func.date(TokenUsage.created_at) == day,
            )
        )

        result = await db.execute(stmt)
        row = result.one()

        return {
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "request_count": row.request_count or 0,
            "cache_hits": row.cache_hits or 0,
            "day": day.isoformat(),
        }

    async def get_user_usage(
        self,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: Optional[str] = None,
        day: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Get token usage for a user from rollup table (O(1)).

        Args:
            db: Database session
            user_id: User to query
            workspace_id: Optional workspace filter
            day: Specific day (None for today)

        Returns:
            Dictionary with usage metrics
        """
        if day is None:
            day = date.today()

        # Query rollup table for fast reads
        stmt = select(TokenUsageRollupDaily).where(
            and_(
                TokenUsageRollupDaily.user_id == user_id,
                TokenUsageRollupDaily.workspace_id == (workspace_id or ""),
                TokenUsageRollupDaily.day == day,
            )
        )

        result = await db.execute(stmt)
        rollup = result.scalar_one_or_none()

        if not rollup:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "request_count": 0,
                "cache_hits": 0,
                "error_count": 0,
                "day": day.isoformat(),
            }

        return {
            "input_tokens": rollup.input_tokens,
            "output_tokens": rollup.output_tokens,
            "request_count": rollup.request_count,
            "cache_hits": rollup.cache_hits,
            "error_count": rollup.error_count,
            "cache_hit_rate": rollup.cache_hit_rate,
            "day": day.isoformat(),
        }

    async def check_budget(
        self,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: Optional[str] = None,
    ) -> Tuple[bool, int, Optional[str]]:
        """
        Check if user is over budget threshold.

        Args:
            db: Database session
            user_id: User to check
            workspace_id: Optional workspace

        Returns:
            Tuple of (over_threshold, percent_used, warning_level)
            warning_level: None, "warning", "critical", or "over"
        """
        # Get or create budget configuration
        budget = await self._get_or_create_budget(db, user_id, workspace_id)

        # Get today's usage from rollup
        usage = await self.get_user_usage(db, user_id, workspace_id)
        total_tokens = usage["input_tokens"] + usage["output_tokens"]

        # Calculate percentage
        if budget.daily_limit == 0:
            percent_used = 0
        else:
            percent_used = int((total_tokens / budget.daily_limit) * 100)

        # Determine warning level
        warning_level = budget.get_warning_level(float(percent_used))
        over_threshold = percent_used >= budget.warning_threshold_percent

        logger.debug(
            "Budget check",
            user_id=str(user_id),
            workspace_id=workspace_id,
            tokens_used=total_tokens,
            daily_limit=budget.daily_limit,
            percent_used=percent_used,
            warning_level=warning_level,
        )

        return over_threshold, percent_used, warning_level

    async def _get_or_create_budget(
        self,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: Optional[str],
    ) -> UserTokenBudget:
        """
        Get or create budget configuration for user/workspace.

        Creates default budget if none exists.
        """
        stmt = select(UserTokenBudget).where(
            and_(
                UserTokenBudget.user_id == user_id,
                UserTokenBudget.workspace_id == workspace_id,
            )
        )

        result = await db.execute(stmt)
        budget = result.scalar_one_or_none()

        if not budget:
            # Create default budget
            budget = UserTokenBudget(
                user_id=user_id,
                workspace_id=workspace_id,
                daily_limit=1000000,  # 1M tokens default
                monthly_limit=30000000,  # 30M tokens default
                warning_threshold_percent=80,
                soft_block=True,  # Only warn by default
            )
            db.add(budget)
            await db.flush()

        return budget

    async def get_thread_usage(
        self,
        db: AsyncSession,
        thread_id: UUID,
        day: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Get token usage for a thread.

        Args:
            db: Database session
            thread_id: Thread to query
            day: Specific day (None for all time)

        Returns:
            Dictionary with usage metrics
        """
        # Build query
        stmt = select(
            func.sum(TokenUsage.input_tokens).label("input_tokens"),
            func.sum(TokenUsage.output_tokens).label("output_tokens"),
            func.count(TokenUsage.id).label("request_count"),
            func.sum(func.cast(TokenUsage.cache_hit, Integer)).label("cache_hits"),
        ).where(TokenUsage.thread_id == thread_id)

        # Add day filter if specified
        if day:
            stmt = stmt.where(func.date(TokenUsage.created_at) == day)

        result = await db.execute(stmt)
        row = result.one()

        return {
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "request_count": row.request_count or 0,
            "cache_hits": row.cache_hits or 0,
            "thread_id": str(thread_id),
            "day": day.isoformat() if day else "all_time",
        }


# Singleton instance
_token_metering_service: Optional[TokenMeteringService] = None


def get_token_metering_service() -> TokenMeteringService:
    """Get singleton token metering service instance."""
    global _token_metering_service
    if _token_metering_service is None:
        _token_metering_service = TokenMeteringService()
    return _token_metering_service
