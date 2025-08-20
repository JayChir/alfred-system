# MCP Servers Directory

## Current Strategy (Phase 1): Off-the-Shelf Official MCPs

We're using official MCP server implementations from NPM packages, deployed in Docker containers on the droplet. This provides immediate functionality with proven, tested implementations.

## Directory Contents

### 🟢 Active (Official Packages in Docker)
These will be simple Docker wrappers around official NPM packages:
- `time/` - Wrapper for @modelcontextprotocol/server-time
- `github/` - Wrapper for @modelcontextprotocol/server-github  
- `fetch/` - Wrapper for @modelcontextprotocol/server-fetch
- `tavily/` - Wrapper for tavily-mcp@0.1.3
- `notion/` - Wrapper for Notion MCP (TBD which package)
- `sequential/` - Wrapper for @modelcontextprotocol/server-sequential-thinking
- `playwright/` - Wrapper for @modelcontextprotocol/server-playwright
- `filesystem/` - Wrapper for @modelcontextprotocol/server-filesystem

### 🟡 Paused/Deprioritized (Custom Python FastMCP)
These custom implementations are preserved for potential future use when we need capabilities beyond what official packages provide:

- **`time/` (Python FastMCP)** - Custom timezone utilities
  - Status: Functional, tested locally
  - Port: 8005
  - May revisit if official package lacks needed features

- **`sequential-thinking/` (Python FastMCP)** - Chain-of-thought reasoning
  - Status: Functional, tested locally  
  - Port: 8007
  - May revisit for custom reasoning capabilities

- **`tavily-search/` (Python FastMCP)** - Started but not completed
  - Status: Partially implemented
  - Port: 8006
  - Paused in favor of official tavily-mcp package

### 🔵 Existing/Keep As-Is
- **`memory/`** - Personal memory MCP (Python)
  - Status: Keep existing implementation
  - This is already custom and working

### 📁 Legacy (From original setup)
- `filesystem/` - Original placeholder
- `github/` - Original placeholder
- `gmail/` - Original placeholder
- `notion/` - Original placeholder
- `shared/` - Shared utilities

## Transition Plan

1. **Phase 1 (Current)**: Deploy official packages in Docker
2. **Phase 2 (Future)**: If specific features are needed that official packages don't provide, revisit our custom FastMCP implementations
3. **Phase 3 (Optional)**: Gradually migrate to custom implementations for performance optimization or unique features

## Why This Approach?

- **Immediate deployment** - Get running in days, not weeks
- **Proven compatibility** - Official packages work with all Claude clients
- **Preserve innovation** - Our custom Python work is saved for when we need it
- **Flexible future** - Can switch between official and custom as needed

## Custom FastMCP Template

When we do need custom servers, we have a proven template in `docs/MCP_SERVER_TEMPLATE.md` that includes:
- FastMCP framework patterns
- Docker configuration
- Port allocation strategy
- Error handling patterns
- Testing approach

## Notes

- All custom Python implementations use FastMCP 2.11+
- Official packages use Node.js 20+
- Port ranges: 3000+ for official, 8000+ for custom
- All servers support Streamable HTTP transport