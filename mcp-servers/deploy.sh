#!/bin/bash
# MCP Servers Deployment Script
# Deploys MCP proxy configuration from git repo to system directories

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located (repo path)
REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && cd .. && pwd )"
MCP_SERVERS_DIR="${REPO_DIR}/mcp-servers"

echo -e "${GREEN}MCP Servers Deployment Script${NC}"
echo -e "Repository path: ${REPO_DIR}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
   echo -e "${RED}Please run as root (use sudo)${NC}"
   exit 1
fi

# Step 1: Install prerequisites
echo -e "${YELLOW}Step 1: Checking prerequisites...${NC}"
if ! command -v node &> /dev/null; then
    echo "Node.js not found. Installing..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

if ! command -v mcp-proxy &> /dev/null; then
    echo "Installing mcp-proxy globally..."
    npm install -g mcp-proxy
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker not found! Please install Docker first.${NC}"
    exit 1
fi

# Step 2: Create required directories
echo -e "${YELLOW}Step 2: Creating directories...${NC}"
mkdir -p /opt/mcp
mkdir -p /etc/nginx/snippets

# Step 3: Deploy systemd service template
echo -e "${YELLOW}Step 3: Deploying systemd service template...${NC}"
# Create a temporary file with the repo path substituted
sed "s|/path/to/alfred-system|${REPO_DIR}|g" "${MCP_SERVERS_DIR}/mcp@.service" > /tmp/mcp@.service
cp /tmp/mcp@.service /etc/systemd/system/mcp@.service
rm /tmp/mcp@.service
echo "Deployed systemd template with repo path: ${REPO_DIR}"

# Step 4: Deploy nginx snippet
echo -e "${YELLOW}Step 4: Deploying nginx configuration snippet...${NC}"
cat > /etc/nginx/snippets/mcp_proxy.conf << 'EOF'
# Streamable HTTP + SSE proxy configuration
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;  # Critical for SSE streaming
proxy_connect_timeout 60s;
proxy_send_timeout 1h;
proxy_read_timeout 1h;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
EOF

# Step 5: Deploy nginx server configs for each MCP server
echo -e "${YELLOW}Step 5: Deploying nginx server configurations...${NC}"
for dir in ${MCP_SERVERS_DIR}/*/; do
    if [ -d "$dir" ]; then
        service_name=$(basename "$dir")
        
        # Skip non-service directories
        if [ "$service_name" = "shared" ]; then
            continue
        fi
        
        nginx_conf="${dir}nginx.conf"
        if [ -f "$nginx_conf" ]; then
            echo "  - Deploying nginx config for: $service_name"
            cp "$nginx_conf" "/etc/nginx/sites-available/mcp-${service_name}.conf"
            ln -sf "/etc/nginx/sites-available/mcp-${service_name}.conf" "/etc/nginx/sites-enabled/"
        fi
    fi
done

# Step 6: Test and reload nginx
echo -e "${YELLOW}Step 6: Testing nginx configuration...${NC}"
if nginx -t; then
    systemctl reload nginx
    echo -e "${GREEN}Nginx configuration valid and reloaded${NC}"
else
    echo -e "${RED}Nginx configuration test failed!${NC}"
    exit 1
fi

# Step 7: Reload systemd
echo -e "${YELLOW}Step 7: Reloading systemd...${NC}"
systemctl daemon-reload

# Step 8: Show available services
echo ""
echo -e "${GREEN}Deployment complete!${NC}"
echo ""
echo "Available MCP services:"
for dir in ${MCP_SERVERS_DIR}/*/; do
    if [ -d "$dir" ]; then
        service_name=$(basename "$dir")
        if [ -f "${dir}${service_name}.env" ]; then
            echo "  - mcp@${service_name}"
        fi
    fi
done

echo ""
echo "To start a service, run:"
echo "  sudo systemctl start mcp@<service-name>"
echo ""
echo "To enable auto-start on boot:"
echo "  sudo systemctl enable mcp@<service-name>"
echo ""
echo "To check service status:"
echo "  sudo systemctl status mcp@<service-name>"
echo ""
echo "Example: sudo systemctl enable --now mcp@time"