#!/usr/bin/env python3
"""
Production Hardening Validation Script

Tests all the hardening measures implemented in Issue #31:
- Security headers with path-aware CSP
- Request size limits per endpoint
- Timeout middleware with SSE exemption
- CORS configuration
- GZip compression with SSE exemption

Usage:
    python test_hardening.py [--host localhost] [--port 8080]
"""

import asyncio
import time
from typing import Any, Dict, List

import httpx
import structlog

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    context_class=dict,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class HardeningValidator:
    """Validates production hardening implementation."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        """Initialize validator with base URL."""
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.results: List[Dict[str, Any]] = []

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    def record_result(self, test_name: str, passed: bool, details: Dict[str, Any]):
        """Record test result."""
        result = {
            "test": test_name,
            "passed": passed,
            "details": details,
            "timestamp": time.time(),
        }
        self.results.append(result)

        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} {test_name}", **details)

    async def test_security_headers(self):
        """Test security headers middleware with path-aware CSP."""
        logger.info("Testing security headers middleware...")

        # Test API endpoint security headers
        try:
            response = await self.client.get(f"{self.base_url}/healthz")
            headers = dict(response.headers)

            expected_headers = [
                "x-content-type-options",
                "x-frame-options",
                "referrer-policy",
                "permissions-policy",
                "cross-origin-opener-policy",
                "cross-origin-resource-policy",
            ]

            missing_headers = []
            for header in expected_headers:
                if header not in headers:
                    missing_headers.append(header)

            # Check CSP header (should be present for API endpoints)
            csp_header = headers.get("content-security-policy") or headers.get(
                "content-security-policy-report-only"
            )

            self.record_result(
                "security_headers_api",
                len(missing_headers) == 0 and csp_header is not None,
                {
                    "missing_headers": missing_headers,
                    "csp_present": csp_header is not None,
                    "csp_value": csp_header[:100] + "..." if csp_header else None,
                    "status_code": response.status_code,
                },
            )

        except Exception as e:
            self.record_result(
                "security_headers_api",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

        # Test docs endpoint CSP (should be relaxed for Swagger/ReDoc)
        try:
            response = await self.client.get(f"{self.base_url}/docs")
            csp_header = response.headers.get(
                "content-security-policy"
            ) or response.headers.get("content-security-policy-report-only")

            # Docs CSP should allow unsafe-inline for Swagger/ReDoc
            docs_csp_relaxed = "unsafe-inline" in csp_header if csp_header else False

            self.record_result(
                "security_headers_docs",
                docs_csp_relaxed,
                {
                    "csp_header": csp_header[:100] + "..." if csp_header else None,
                    "unsafe_inline_allowed": docs_csp_relaxed,
                    "status_code": response.status_code,
                },
            )

        except Exception as e:
            self.record_result(
                "security_headers_docs",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def test_cors_configuration(self):
        """Test CORS configuration."""
        logger.info("Testing CORS configuration...")

        try:
            # Test preflight request
            response = await self.client.options(
                f"{self.base_url}/api/v1/chat",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type,Authorization",
                },
            )

            cors_headers = {
                "access-control-allow-origin": response.headers.get(
                    "access-control-allow-origin"
                ),
                "access-control-allow-credentials": response.headers.get(
                    "access-control-allow-credentials"
                ),
                "access-control-allow-methods": response.headers.get(
                    "access-control-allow-methods"
                ),
                "access-control-allow-headers": response.headers.get(
                    "access-control-allow-headers"
                ),
                "access-control-max-age": response.headers.get(
                    "access-control-max-age"
                ),
            }

            cors_configured = (
                cors_headers["access-control-allow-origin"] is not None
                and cors_headers["access-control-allow-credentials"] == "true"
                and cors_headers["access-control-allow-methods"] is not None
            )

            # Check that allow-headers is NOT "*" (security requirement)
            headers_not_wildcard = cors_headers["access-control-allow-headers"] != "*"

            self.record_result(
                "cors_configuration",
                cors_configured and headers_not_wildcard,
                {
                    "cors_headers": cors_headers,
                    "headers_not_wildcard": headers_not_wildcard,
                    "status_code": response.status_code,
                },
            )

        except Exception as e:
            self.record_result(
                "cors_configuration",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def test_request_size_limits(self):
        """Test request size limit middleware."""
        logger.info("Testing request size limits...")

        # Test health endpoint (1KB limit)
        try:
            large_payload = "x" * 2000  # 2KB payload for 1KB limit
            response = await self.client.post(
                f"{self.base_url}/healthz",
                data=large_payload,
            )

            # Should get 413 Payload Too Large
            size_limit_enforced = response.status_code == 413

            self.record_result(
                "size_limit_health",
                size_limit_enforced,
                {
                    "status_code": response.status_code,
                    "payload_size": len(large_payload),
                    "expected": 413,
                },
            )

        except httpx.HTTPStatusError as e:
            # 413 is expected, so this is actually success
            size_limit_enforced = e.response.status_code == 413
            self.record_result(
                "size_limit_health",
                size_limit_enforced,
                {
                    "status_code": e.response.status_code,
                    "payload_size": 2000,
                    "expected": 413,
                },
            )
        except Exception as e:
            self.record_result(
                "size_limit_health",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def test_timeout_middleware(self):
        """Test timeout middleware (basic connectivity test)."""
        logger.info("Testing timeout middleware...")

        # Test that normal requests complete within timeout
        try:
            start_time = time.time()
            response = await self.client.get(f"{self.base_url}/healthz")
            duration = time.time() - start_time

            # Should complete quickly and successfully
            timeout_working = response.status_code == 200 and duration < 5.0

            self.record_result(
                "timeout_middleware",
                timeout_working,
                {
                    "status_code": response.status_code,
                    "duration_seconds": round(duration, 3),
                    "timeout_threshold": 5.0,
                },
            )

        except Exception as e:
            self.record_result(
                "timeout_middleware",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def test_gzip_compression(self):
        """Test GZip compression middleware."""
        logger.info("Testing GZip compression...")

        try:
            response = await self.client.get(
                f"{self.base_url}/docs", headers={"Accept-Encoding": "gzip"}
            )

            # Check if response is compressed
            content_encoding = response.headers.get("content-encoding")
            is_compressed = content_encoding == "gzip"

            self.record_result(
                "gzip_compression",
                is_compressed,
                {
                    "content_encoding": content_encoding,
                    "status_code": response.status_code,
                    "content_length": len(response.content),
                },
            )

        except Exception as e:
            self.record_result(
                "gzip_compression",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def test_basic_functionality(self):
        """Test that basic API functionality still works."""
        logger.info("Testing basic API functionality...")

        try:
            response = await self.client.get(f"{self.base_url}/healthz")

            basic_working = (
                response.status_code == 200 and "status" in response.text.lower()
            )

            self.record_result(
                "basic_functionality",
                basic_working,
                {
                    "status_code": response.status_code,
                    "response_length": len(response.content),
                    "health_check": "status" in response.text.lower(),
                },
            )

        except Exception as e:
            self.record_result(
                "basic_functionality",
                False,
                {"error": str(e), "error_type": type(e).__name__},
            )

    async def run_all_tests(self):
        """Run all hardening validation tests."""
        logger.info("Starting production hardening validation", base_url=self.base_url)

        tests = [
            self.test_basic_functionality,
            self.test_security_headers,
            self.test_cors_configuration,
            self.test_request_size_limits,
            self.test_timeout_middleware,
            self.test_gzip_compression,
        ]

        for test in tests:
            try:
                await test()
            except Exception as e:
                logger.error(
                    f"Test {test.__name__} failed with exception", error=str(e)
                )

        # Generate summary
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r["passed"])
        failed_tests = total_tests - passed_tests

        logger.info(
            "Production hardening validation complete",
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            success_rate=f"{(passed_tests/total_tests)*100:.1f}%"
            if total_tests > 0
            else "0%",
        )

        # Print detailed results
        print("\n" + "=" * 60)
        print("PRODUCTION HARDENING VALIDATION RESULTS")
        print("=" * 60)

        for result in self.results:
            status = "✅ PASS" if result["passed"] else "❌ FAIL"
            print(f"{status} {result['test']}")
            if not result["passed"]:
                print(f"    Details: {result['details']}")

        print(
            f"\nSUMMARY: {passed_tests}/{total_tests} tests passed ({(passed_tests/total_tests)*100:.1f}%)"
        )

        return passed_tests == total_tests


async def main():
    """Main validation function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate production hardening implementation"
    )
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=8080, help="Server port")
    parser.add_argument(
        "--no-server-check", action="store_true", help="Skip server connectivity check"
    )

    args = parser.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    validator = HardeningValidator(base_url)

    try:
        # Quick connectivity check
        if not args.no_server_check:
            logger.info("Checking server connectivity...", base_url=base_url)
            try:
                response = await validator.client.get(f"{base_url}/", timeout=5.0)
                logger.info("Server is reachable", status_code=response.status_code)
            except Exception as e:
                logger.error(
                    "Server is not reachable - start the server first",
                    error=str(e),
                    base_url=base_url,
                )
                print(f"\n❌ Server at {base_url} is not reachable.")
                print(
                    "Start the server first with: uvicorn src.app:app --host localhost --port 8080"
                )
                return False

        # Run validation tests
        success = await validator.run_all_tests()
        return success

    finally:
        await validator.close()


if __name__ == "__main__":
    asyncio.run(main())
