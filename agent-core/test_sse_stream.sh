#!/bin/bash

# Test script for SSE streaming endpoint
# Usage: ./test_sse_stream.sh

echo "Testing SSE streaming endpoint..."
echo "==============================="

# Check if API_KEY is set
if [ -z "$API_KEY" ]; then
    echo "Error: API_KEY environment variable is not set"
    echo "Please set it: export API_KEY=your-api-key"
    exit 1
fi

# Test URL
BASE_URL="http://localhost:8080"
STREAM_URL="$BASE_URL/chat/stream"

echo "Testing: $STREAM_URL"
echo ""

# Test 1: Basic SSE streaming
echo "Test 1: Basic SSE streaming with heartbeat"
echo "-------------------------------------------"
echo "Sending prompt: 'What is 2+2?'"
echo ""
echo "Starting stream (will run for 10 seconds to test heartbeat)..."

# Use curl with timeout to test SSE
timeout 10 curl -N \
    -H "X-API-Key: $API_KEY" \
    -H "Accept: text/event-stream" \
    -H "Cache-Control: no-cache" \
    "$STREAM_URL?prompt=What%20is%202%2B2%3F" 2>/dev/null | while IFS= read -r line; do
    if [[ $line == event:* ]]; then
        echo "[EVENT] $line"
    elif [[ $line == data:* ]]; then
        echo "[DATA]  $line"
    elif [[ -z "$line" ]]; then
        echo "[END]"
    fi
done

echo ""
echo "Test 2: Error handling"
echo "----------------------"
echo "Testing with invalid API key..."

curl -N \
    -H "X-API-Key: invalid-key" \
    -H "Accept: text/event-stream" \
    "$STREAM_URL?prompt=test" 2>/dev/null | head -5

echo ""
echo "Test 3: Connection headers"
echo "--------------------------"
echo "Checking response headers..."

curl -I \
    -H "X-API-Key: $API_KEY" \
    -H "Accept: text/event-stream" \
    "$STREAM_URL?prompt=test" 2>/dev/null | grep -E "(Content-Type|Cache-Control|Connection|X-Accel-Buffering)"

echo ""
echo "==============================="
echo "SSE streaming tests complete!"
echo ""
echo "Expected results:"
echo "- Event types: token, tool_call, tool_result, warning, done, heartbeat"
echo "- Heartbeat events every 30 seconds"
echo "- Proper SSE format with 'event:' and 'data:' lines"
echo "- Headers: text/event-stream, no-cache, keep-alive"
