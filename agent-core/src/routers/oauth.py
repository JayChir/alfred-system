"""
OAuth endpoints for Alfred Agent Core.

This module provides OAuth endpoints for external service integrations:
- /connect/{provider} - Initiate OAuth flow with redirect to provider
- /oauth/{provider}/callback - Handle OAuth callback and token exchange

The OAuth tokens obtained here are used to authenticate MCP client connections
to hosted services (e.g., Notion's hosted MCP at https://mcp.notion.com/mcp).

Security features:
- CSRF protection with cryptographically secure state tokens
- Flow session binding and validation
- Comprehensive error handling with structured logging
- Encrypted token storage immediately after exchange

Supported providers:
- Notion: Backend OAuth with client credentials for hosted MCP access
"""

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_db_session
from ..services.oauth_manager import (
    OAuthManager,
    OAuthManagerError,
    StateValidationError,
    TokenExchangeError,
)
from ..utils.crypto import CryptoService

logger = structlog.get_logger(__name__)

# Create router for OAuth endpoints
router = APIRouter(prefix="/oauth", tags=["oauth"])

# OAuth error taxonomy mapping for consistent error responses
OAUTH_ERROR_MAPPING = {
    # User cancellation/denial
    "access_denied": ("OAUTH-ACCESS-DENIED", "User denied authorization"),
    # Validation errors
    "invalid_request": ("OAUTH-EXCHANGE-FAIL", "Invalid OAuth request"),
    "invalid_client": ("OAUTH-EXCHANGE-FAIL", "Invalid client credentials"),
    "invalid_grant": ("OAUTH-EXCHANGE-FAIL", "Invalid or expired authorization grant"),
    "unauthorized_client": (
        "OAUTH-EXCHANGE-FAIL",
        "Client not authorized for this grant type",
    ),
    "unsupported_grant_type": ("OAUTH-EXCHANGE-FAIL", "Unsupported grant type"),
    "invalid_scope": ("OAUTH-EXCHANGE-FAIL", "Invalid or unauthorized scope"),
    # Server errors
    "server_error": ("OAUTH-EXCHANGE-FAIL", "OAuth provider server error"),
    "temporarily_unavailable": (
        "OAUTH-EXCHANGE-FAIL",
        "OAuth provider temporarily unavailable",
    ),
}


def get_oauth_manager(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> OAuthManager:  # noqa: B008
    """
    Dependency to get OAuth Manager instance.

    Args:
        settings: Application settings

    Returns:
        OAuth Manager with crypto service
    """
    crypto_service = CryptoService(settings.fernet_key)
    return OAuthManager(settings, crypto_service)


def create_error_response(
    error_code: str,
    message: str,
    origin: str = "oauth",
    request_id: Optional[str] = None,
) -> dict:
    """
    Create standardized error response following MVP error taxonomy.

    Args:
        error_code: Error code from taxonomy (e.g., OAUTH-ACCESS-DENIED)
        message: Human-readable error message
        origin: Component where error occurred
        request_id: Request ID for tracing

    Returns:
        Structured error response
    """
    return {
        "error": error_code,
        "message": message,
        "origin": origin,
        "requestId": request_id or "unknown",
    }


def create_success_page(
    provider: str, workspace_name: Optional[str] = None, return_to: Optional[str] = None
) -> str:
    """
    Create simple HTML success page for OAuth completion.

    Args:
        provider: OAuth provider name
        workspace_name: Connected workspace name (if available)
        return_to: Optional return URL for navigation

    Returns:
        HTML content for success page
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connection Successful - Alfred Agent</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                   margin: 0; padding: 40px; background: #f5f5f5; color: #333; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white;
                         border-radius: 8px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #00a86b; margin-bottom: 20px; }}
            .success-icon {{ font-size: 48px; color: #00a86b; margin-bottom: 20px; }}
            .details {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            .button {{ display: inline-block; background: #007bff; color: white;
                     padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
            .button:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">✅</div>
            <h1>{provider.title()} Connected Successfully!</h1>

            <p>Your {provider.title()} account has been successfully connected to Alfred Agent.</p>

            {f'<div class="details"><strong>Connected Workspace:</strong> {workspace_name}</div>' if workspace_name else ''}

            <p>You can now:</p>
            <ul>
                <li>Access your {provider.title()} data through Alfred Agent</li>
                <li>Use {provider.title()} tools in chat conversations</li>
                <li>Manage your connection in settings</li>
            </ul>

            {f'<a href="{return_to}" class="button">Continue</a>' if return_to else '<button class="button" onclick="window.close()">Close</button>'}
        </div>
    </body>
    </html>
    """


def create_error_page(
    error_code: str, message: str, provider: str, details: Optional[str] = None
) -> str:
    """
    Create simple HTML error page for OAuth failures.

    Args:
        error_code: Error code from taxonomy
        message: Human-readable error message
        provider: OAuth provider name
        details: Optional additional details

    Returns:
        HTML content for error page
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connection Failed - Alfred Agent</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                   margin: 0; padding: 40px; background: #f5f5f5; color: #333; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white;
                         border-radius: 8px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #dc3545; margin-bottom: 20px; }}
            .error-icon {{ font-size: 48px; color: #dc3545; margin-bottom: 20px; }}
            .error-details {{ background: #f8d7da; color: #721c24; padding: 15px;
                            border-radius: 5px; margin: 20px 0; border: 1px solid #f5c6cb; }}
            .button {{ display: inline-block; background: #007bff; color: white;
                     padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
            .button:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="error-icon">❌</div>
            <h1>Connection Failed</h1>

            <p>We couldn't connect your {provider.title()} account to Alfred Agent.</p>

            <div class="error-details">
                <strong>Error:</strong> {message}
                {f'<br><br><strong>Details:</strong> {details}' if details else ''}
            </div>

            <p>Please try again or contact support if the problem persists.</p>

            <button class="button" onclick="window.close()">Close</button>
        </div>
    </body>
    </html>
    """


# ===== OAuth Initiation Endpoints =====


@router.get("/connect/notion", response_class=RedirectResponse)
async def connect_notion(
    request: Request,
    return_to: Optional[str] = Query(
        None, description="Return URL after successful auth"
    ),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    oauth_manager: OAuthManager = Depends(get_oauth_manager),  # noqa: B008
):
    """
    Initiate Notion OAuth flow by redirecting to Notion authorization endpoint.

    This endpoint:
    1. Generates cryptographically secure state token
    2. Stores state in database with TTL and user binding
    3. Builds Notion authorization URL with required parameters
    4. Redirects user to Notion for consent

    Args:
        request: FastAPI request object
        return_to: Optional return URL after successful authentication
        db: Database session dependency
        oauth_manager: OAuth manager service dependency

    Returns:
        RedirectResponse to Notion authorization endpoint
    """
    request_id = getattr(request.state, "request_id", "unknown")

    try:
        logger.info(
            "Starting Notion OAuth flow",
            request_id=request_id,
            user_agent=request.headers.get("user-agent", "unknown")[:100],
            return_to=return_to,
        )

        # Extract flow session identifier for OAuth CSRF binding
        flow_session_id = request.headers.get("X-Flow-Session-ID", f"flow_{request_id}")
        user_id = None  # TODO: Extract from actual authentication system

        # Create OAuth state record with CSRF protection
        oauth_state = await oauth_manager.create_oauth_state(
            db=db,
            provider="notion",
            user_id=user_id,
            flow_session_id=flow_session_id,
            return_to=return_to,
        )

        # Build Notion authorization URL with state token
        authorization_url = oauth_manager.build_notion_authorization_url(
            state_token=oauth_state.state
        )

        logger.info(
            "Redirecting to Notion authorization",
            request_id=request_id,
            state_id=str(oauth_state.id),
            redirect_url=authorization_url[:100] + "..."
            if len(authorization_url) > 100
            else authorization_url,
        )

        # Redirect user to Notion for authorization
        return RedirectResponse(url=authorization_url, status_code=302)

    except OAuthManagerError as e:
        logger.error("OAuth configuration error", request_id=request_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=create_error_response(
                error_code="OAUTH-CONFIG-ERROR",
                message="OAuth configuration error",
                request_id=request_id,
            ),
        ) from e
    except Exception as e:
        logger.error(
            "Unexpected error starting OAuth flow", request_id=request_id, error=str(e)
        )
        raise HTTPException(
            status_code=500,
            detail=create_error_response(
                error_code="APP-5XX",
                message="Internal server error",
                request_id=request_id,
            ),
        ) from e


# ===== OAuth Callback Endpoints =====


@router.get("/notion/callback", response_class=HTMLResponse)
async def notion_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None, description="Authorization code from Notion"),
    state: Optional[str] = Query(None, description="CSRF state token"),
    error: Optional[str] = Query(
        None, description="Error code if authorization failed"
    ),
    error_description: Optional[str] = Query(
        None, description="Error description from Notion"
    ),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    oauth_manager: OAuthManager = Depends(get_oauth_manager),  # noqa: B008
):
    """
    Handle Notion OAuth callback and complete token exchange.

    This endpoint:
    1. Validates state token for CSRF protection
    2. Handles user cancellation/errors gracefully
    3. Exchanges authorization code for tokens via HTTP Basic auth
    4. Stores encrypted tokens in database
    5. Optional token validation via /v1/users/me
    6. Returns success/failure HTML page

    Args:
        request: FastAPI request object
        code: Authorization code from Notion (if successful)
        state: CSRF state token to validate
        error: Error code if authorization was denied
        error_description: Human-readable error description
        db: Database session dependency
        oauth_manager: OAuth manager service dependency

    Returns:
        HTMLResponse with success or error page
    """
    request_id = getattr(request.state, "request_id", "unknown")

    try:
        logger.info(
            "Processing Notion OAuth callback",
            request_id=request_id,
            has_code=bool(code),
            has_error=bool(error),
            state_token=state[:8] + "..." if state else None,
        )

        # Handle authorization errors (user cancellation, etc.)
        if error:
            error_code, error_message = OAUTH_ERROR_MAPPING.get(
                error, ("OAUTH-EXCHANGE-FAIL", f"OAuth error: {error}")
            )

            logger.warning(
                "OAuth authorization error",
                request_id=request_id,
                error=error,
                error_description=error_description,
            )

            # Clean up state token if present
            if state:
                try:
                    await oauth_manager.validate_and_consume_state(
                        db=db, state_token=state, provider="notion"
                    )
                except StateValidationError:
                    pass  # State already invalid, ignore cleanup failure

            return HTMLResponse(
                content=create_error_page(
                    error_code=error_code,
                    message=error_message,
                    provider="notion",
                    details=error_description,
                ),
                status_code=400,
            )

        # Validate required parameters for successful flow
        if not code or not state:
            logger.warning(
                "Missing required OAuth callback parameters",
                request_id=request_id,
                has_code=bool(code),
                has_state=bool(state),
            )

            return HTMLResponse(
                content=create_error_page(
                    error_code="OAUTH-EXCHANGE-FAIL",
                    message="Missing required authorization parameters",
                    provider="notion",
                ),
                status_code=400,
            )

        # Validate and consume state token
        flow_session_id = request.headers.get("X-Flow-Session-ID", f"flow_{request_id}")

        try:
            oauth_state = await oauth_manager.validate_and_consume_state(
                db=db,
                state_token=state,
                provider="notion",
                flow_session_id=flow_session_id,
            )
        except StateValidationError as e:
            logger.warning(
                "OAuth state validation failed", request_id=request_id, error=str(e)
            )

            return HTMLResponse(
                content=create_error_page(
                    error_code="OAUTH-EXCHANGE-FAIL",
                    message="Invalid or expired authorization state",
                    provider="notion",
                    details="Please try connecting again",
                ),
                status_code=400,
            )

        # Exchange authorization code for tokens
        try:
            async with oauth_manager:  # Ensure HTTP client cleanup
                token_response = await oauth_manager.exchange_notion_code_for_tokens(
                    authorization_code=code
                )
        except TokenExchangeError as e:
            logger.error("Token exchange failed", request_id=request_id, error=str(e))

            return HTMLResponse(
                content=create_error_page(
                    error_code="OAUTH-EXCHANGE-FAIL",
                    message="Failed to exchange authorization code for tokens",
                    provider="notion",
                    details="Please try connecting again",
                ),
                status_code=500,
            )

        # Store encrypted connection record
        # TODO: Use actual user ID from authentication system
        user_id = oauth_state.user_id or "anonymous"

        try:
            connection = await oauth_manager.store_notion_connection(
                db=db, user_id=user_id, token_response=token_response
            )
        except Exception as e:
            logger.error(
                "Failed to store connection", request_id=request_id, error=str(e)
            )

            return HTMLResponse(
                content=create_error_page(
                    error_code="APP-5XX",
                    message="Failed to store connection",
                    provider="notion",
                    details="Please try connecting again",
                ),
                status_code=500,
            )

        # Optional: Validate token by calling Notion /v1/users/me
        access_token = token_response["access_token"]
        user_validation = None

        try:
            async with oauth_manager:
                user_validation = await oauth_manager.validate_notion_token(
                    access_token
                )
        except Exception:
            pass  # Non-critical, continue with success

        logger.info(
            "Notion OAuth flow completed successfully",
            request_id=request_id,
            connection_id=str(connection.id),
            bot_id=connection.bot_id,
            workspace_id=connection.workspace_id,
            token_validated=bool(user_validation),
        )

        # Create success page
        return HTMLResponse(
            content=create_success_page(
                provider="notion",
                workspace_name=connection.workspace_name,
                return_to=oauth_state.return_to,
            ),
            status_code=200,
        )

    except Exception as e:
        logger.error(
            "Unexpected error in OAuth callback", request_id=request_id, error=str(e)
        )

        return HTMLResponse(
            content=create_error_page(
                error_code="APP-5XX",
                message="Internal server error during OAuth processing",
                provider="notion",
            ),
            status_code=500,
        )
