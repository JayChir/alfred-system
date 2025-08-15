# Contributing to Alfred System

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/JayChir/alfred-system.git
   cd alfred-system
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

3. **Start development environment**
   ```bash
   docker-compose up -d
   ```

## Project Structure

```
alfred-system/
├── agent/                 # Main Alfred agent (FastAPI + Pydantic AI)
├── mcp-servers/          # Custom MCP servers
│   ├── memory/           # Working memory MCP
│   ├── filesystem/       # File operations MCP
│   ├── notion/           # Notion integration MCP
│   └── github/           # GitHub integration MCP
├── web/                  # Next.js PWA interface
├── cli/                  # Python CLI interface
├── nginx/               # Nginx configuration
├── db/                  # Database initialization scripts
└── docs/                # Additional documentation
```

## Development Phases

Currently in **Phase 0: Infrastructure Setup** ✅

### Phase 1: MCP Infrastructure (Next)
- [ ] Memory MCP server implementation
- [ ] Filesystem MCP server
- [ ] Notion MCP customization
- [ ] GitHub MCP enhancements

### Phase 2: Agent Core
- [ ] FastAPI application structure
- [ ] Pydantic AI integration
- [ ] MCP-to-Pydantic tool adapter
- [ ] Streaming and session management

## Code Style

- Python: Follow PEP 8, use Black for formatting
- TypeScript: Prettier + ESLint configuration
- Commit messages: Conventional commits format
- Documentation: Keep docs updated with changes

## Testing

- Python: pytest for backend testing
- TypeScript: Jest for frontend testing
- Integration: docker-compose test environment
- E2E: Playwright for full system testing

## Deployment

Production deployment uses the same Docker compose setup on DigitalOcean with:
- Nginx SSL termination
- Let's Encrypt certificates
- PostgreSQL with persistent volumes
- Health checks and monitoring

---

*This is a learning project focused on cloud infrastructure, AI integration, and system design. Contributions welcome!*