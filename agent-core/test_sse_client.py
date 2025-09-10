#!/usr/bin/env python3
"""
Test client for SSE streaming endpoint.

This demonstrates how to consume the SSE stream with proper event handling
and automatic reconnection support.
"""

import argparse
import json
import os
import sys
import time
from typing import Optional

import requests
import sseclient  # pip install sseclient-py


def test_sse_stream(
    base_url: str = "http://localhost:8080",
    api_key: Optional[str] = None,
    prompt: str = "What is the capital of France?",
    timeout: int = 60,
):
    """
    Test the SSE streaming endpoint.

    Args:
        base_url: Base URL of the API
        api_key: API key for authentication
        prompt: Test prompt to send
        timeout: Max time to stream (seconds)
    """
    if not api_key:
        api_key = os.getenv("API_KEY")
        if not api_key:
            print("Error: API_KEY not provided and not in environment")
            sys.exit(1)

    url = f"{base_url}/chat/stream"
    headers = {
        "X-API-Key": api_key,
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    params = {"prompt": prompt}

    print(f"Testing SSE stream: {url}")
    print(f"Prompt: {prompt}")
    print("-" * 50)

    start_time = time.time()
    event_counts = {
        "token": 0,
        "tool_call": 0,
        "tool_result": 0,
        "warning": 0,
        "done": 0,
        "heartbeat": 0,
        "error": 0,
    }

    try:
        # Create SSE client
        response = requests.get(url, headers=headers, params=params, stream=True)
        response.raise_for_status()

        client = sseclient.SSEClient(response)

        print("Stream connected. Listening for events...\n")

        for event in client.events():
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed > timeout:
                print(f"\nTimeout reached ({timeout}s)")
                break

            # Parse event
            event_type = event.event or "message"
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

            # Display event
            print(f"[{elapsed:.1f}s] Event: {event_type}")

            if event.data:
                try:
                    data = json.loads(event.data)

                    # Handle different event types
                    if event_type == "token":
                        content = data.get("content", "")
                        print(f"  Token: {content}", end="", flush=True)

                    elif event_type == "tool_call":
                        tool = data.get("tool", "unknown")
                        args = data.get("args", {})
                        print(f"  Tool: {tool}")
                        print(f"  Args: {json.dumps(args, indent=2)}")

                    elif event_type == "tool_result":
                        tool = data.get("tool", "unknown")
                        result = data.get("result", "")
                        print(f"  Tool: {tool}")
                        print(f"  Result: {result[:100]}...")

                    elif event_type == "warning":
                        level = data.get("level", "unknown")
                        message = data.get("message", "")
                        print(f"  Level: {level}")
                        print(f"  Message: {message}")

                    elif event_type == "done":
                        usage = data.get("usage", {})
                        cache_hit = data.get("cache_hit", False)
                        print(f"  Cache Hit: {cache_hit}")
                        print(f"  Usage: {json.dumps(usage)}")
                        print("\nStream completed successfully!")
                        break

                    elif event_type == "heartbeat":
                        timestamp = data.get("timestamp", "")
                        print(f"  Timestamp: {timestamp}")

                    elif event_type == "error":
                        error = data.get("error", "Unknown error")
                        print(f"  Error: {error}")
                        break

                    else:
                        print(f"  Data: {json.dumps(data, indent=2)}")

                except json.JSONDecodeError:
                    print(f"  Raw data: {event.data}")

            print()  # Blank line between events

    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    finally:
        print("\n" + "=" * 50)
        print("Event Summary:")
        for event_type, count in event_counts.items():
            if count > 0:
                print(f"  {event_type}: {count}")
        print(f"Total time: {time.time() - start_time:.1f}s")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Test SSE streaming endpoint")
    parser.add_argument(
        "--url", default="http://localhost:8080", help="Base URL of the API"
    )
    parser.add_argument("--api-key", help="API key (or set API_KEY env var)")
    parser.add_argument(
        "--prompt",
        default="What is the capital of France? Also tell me about its history.",
        help="Prompt to send",
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="Max time to stream (seconds)"
    )

    args = parser.parse_args()

    test_sse_stream(
        base_url=args.url,
        api_key=args.api_key,
        prompt=args.prompt,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
