#!/bin/bash
# Helper script for building Docker images in Alfred System

echo "================================================"
echo "Alfred System Docker Build Helper"
echo "================================================"
echo ""
echo "⚠️  IMPORTANT: If you're behind Zscaler/corporate proxy:"
echo "   1. Temporarily disable Zscaler"
echo "   2. Run this script"
echo "   3. Re-enable Zscaler after build completes"
echo ""
echo "Building in 5 seconds... (Ctrl+C to cancel)"
echo ""

sleep 5

# Build all MCP servers
echo "Building MCP servers..."

# Time MCP
if [ -d "mcp-servers/time" ]; then
    echo "Building Time MCP server..."
    docker build -t alfred-time-mcp mcp-servers/time/
fi

# Add more servers as they're implemented
# if [ -d "mcp-servers/brave-search" ]; then
#     echo "Building Brave Search MCP server..."
#     docker build -t alfred-brave-search-mcp mcp-servers/brave-search/
# fi

echo ""
echo "✅ Build complete! You can now re-enable Zscaler if needed."