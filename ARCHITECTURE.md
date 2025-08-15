# Alfred System Architecture

## Overview

Alfred System is designed as a cloud-native, model-agnostic AI assistant platform with the following key principles:

- **Unified Context**: Single source of truth for all AI interactions
- **Model Agnostic**: Easy switching between LLM providers (DeepSeek, OpenAI, Anthropic, local models)
- **MCP-First**: Built around Model Context Protocol for tool integration
- **Multi-Interface**: Support for Claude Desktop, web, mobile, and terminal access

## High-Level Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Claude Desktop  │     │   Web/Mobile     │     │  Console (SSH)    │
│   (Your Mac)     │     │     Agent        │     │   via Termius     │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                         │                         │
         └─────────────────────────┴─────────────────────────┘
                                   │
                          [HTTPS + SSE/WebSocket]
                                   │
    ┌──────────────────────────────────────────────────────────────┐
    │                   DigitalOcean Droplet                        │
    ├────────────────────────────────────────────────────────────────┤
    │  Nginx (Reverse Proxy) → Port 80/443                         │
    │    ├── api.domain.com → Alfred Agent (8000)                   │
    │    ├── chat.domain.com → Web Interface (3000)                 │
    │    └── mcp.domain.com → MCP Servers (8001-8004)               │
    │                                                               │
    │  Alfred Agent (FastAPI + Pydantic AI)                         │
    │    ├── Model Integration (DeepSeek/OpenAI/Anthropic)          │
    │    ├── MCP Client Connections                                 │
    │    ├── Session Management                                     │
    │    └── Streaming Responses (SSE)                              │
    │                                                               │
    │  MCP Servers (FastMCP)                                        │
    │    ├── Memory MCP (8001) - Working memory & relationships     │
    │    ├── Filesystem MCP (8002) - File operations               │
    │    ├── Notion MCP (8003) - Knowledge graph integration       │
    │    └── GitHub MCP (8004) - Repository management             │
    │                                                               │
    │  PostgreSQL Database                                          │
    │    ├── Session data & context                                │
    │    ├── Working memory captures                               │
    │    └── Configuration & audit logs                            │
    └──────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Agent Orchestration Layer
- **Framework**: Python with FastAPI
- **Agent Framework**: Pydantic AI for model-agnostic tool integration
- **Model Provider**: Single provider (DeepSeek initially, easy swapping)
- **Tool Integration**: MCP-to-Pydantic tool conversion
- **Streaming**: Server-Sent Events (SSE) via Pydantic AI
- **Session Management**: PostgreSQL backed

### 2. MCP Infrastructure
- **All MCP Servers in Python**: Using FastMCP framework
- **Unified Access**: Both Claude Desktop and web agent use same servers
- **Servers Planned**:
  - Memory MCP (PostgreSQL backed working memory)
  - Filesystem MCP (secure file operations)
  - Notion MCP (custom implementation for GTD workflows)
  - GitHub MCP (enhanced repository management)
  - Future: Gmail/Calendar, custom workflow MCPs

### 3. Client Interfaces
- **Web/Mobile**: Next.js Progressive Web App with streaming chat
- **Desktop**: Claude Desktop with MCP proxy client
- **Terminal**: Python Rich CLI for console access
- **API**: RESTful + streaming endpoints

### 4. Data Layer
- **PostgreSQL**: All persistent data (sessions, working memory, configuration)
- **JSONB**: Flexible schema for memory storage and relationships
- **Notion**: Primary knowledge graph and long-term storage (external)
- **File System**: Temporary files and logs

## Model-Agnostic Design

The system uses Pydantic AI to make MCP tools work with any LLM provider:

```python
# Easy model swapping
model = DeepSeekModel()  # or OpenAIModel('gpt-4'), AnthropicModel()

# MCP tools become model-agnostic via adapter
class MCPToolAdapter:
    def __init__(self, mcp_client):
        self.mcp = mcp_client
    
    def to_pydantic_tools(self):
        """Convert MCP tools to work with any model"""
        tools = []
        for mcp_tool in self.mcp.list_tools():
            @tool(name=mcp_tool.name)
            def adapted_tool(params: mcp_tool.schema):
                return self.mcp.call_tool(mcp_tool.name, params)
            tools.append(adapted_tool)
        return tools

# Agent with model-agnostic tools
agent = Agent(
    model=model,
    tools=mcp_adapter.to_pydantic_tools()
)
```

## Deployment Architecture

### Docker-First Approach
- All services containerized from day 1
- docker-compose for local development
- Easy migration between platforms
- Consistent dev/prod environments

### Service Layout
```yaml
Services:
  nginx:80,443          # Reverse proxy & SSL termination
  alfred-agent:8000     # Main agent server
  web:3000             # Next.js PWA
  memory-mcp:8001      # Working memory
  filesystem-mcp:8002  # File operations
  notion-mcp:8003      # Knowledge graph
  github-mcp:8004      # Repository management
  postgres:5432        # Database
```

### Security & Access
- SSL certificates via Let's Encrypt
- API key authentication initially
- JWT tokens for session management
- OAuth2 planned for multi-user expansion

## Implementation Phases

1. **Phase 0**: Infrastructure Setup ✅
2. **Phase 1**: MCP Infrastructure (Days 3-7)
3. **Phase 2**: Agent Core (Days 8-12)
4. **Phase 3**: Claude Desktop Integration (Days 13-14)
5. **Phase 4**: User Interfaces (Days 15-18)
6. **Phase 5**: Integration & Polish (Days 19-21)

## Technology Stack

### Backend
- **Language**: Python 3.11+
- **Web Framework**: FastAPI
- **Agent Framework**: Pydantic AI
- **MCP Framework**: FastMCP
- **Database**: PostgreSQL 15
- **Async**: asyncio/uvicorn

### Frontend
- **Web**: Next.js 14+ with TypeScript
- **Styling**: TailwindCSS
- **PWA**: Service workers, offline support
- **CLI**: Python Rich for terminal interface

### Infrastructure
- **Hosting**: DigitalOcean Premium AMD
- **Containers**: Docker + docker-compose
- **Reverse Proxy**: Nginx
- **SSL**: Let's Encrypt via Certbot
- **DNS**: Cloudflare

### Integrations
- **Models**: DeepSeek (primary), OpenAI, Anthropic
- **Knowledge**: Notion API
- **Code**: GitHub API
- **Communication**: Email/Calendar APIs (future)

## Performance Targets

- **Response Latency**: <3 seconds for most operations
- **Streaming**: Real-time response chunks via SSE
- **Uptime**: 99.5% availability target
- **Resource Usage**: 70% max CPU/memory utilization
- **Cost**: <$35/month operational expenses

## Scaling Considerations

### Current (Single User)
- 4GB RAM, 2 vCPU sufficient
- PostgreSQL handles expected load
- All services on single droplet

### Future (Multi-User)
- Horizontal scaling with load balancer
- Database connection pooling
- Redis for session storage
- Kubernetes migration path available

---

*This architecture balances simplicity for rapid development with flexibility for future growth, following cloud-native principles while maintaining cost efficiency.*