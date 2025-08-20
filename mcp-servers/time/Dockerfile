FROM python:3.11-slim

WORKDIR /app

# For corporate proxy/Zscaler environments
# Mount certificate at build time: docker build --build-arg CERT_PATH=/path/to/cert ...
ARG CERT_PATH
COPY ${CERT_PATH:-/dev/null} /tmp/zscaler.crt
RUN if [ -f /tmp/zscaler.crt ] && [ -s /tmp/zscaler.crt ]; then \
        cp /tmp/zscaler.crt /usr/local/share/ca-certificates/ && \
        update-ca-certificates; \
    fi

# Configure pip to use system certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Expose FastMCP default port
EXPOSE 8000

# Run the server
CMD ["python", "-m", "uvicorn", "src.server:mcp", "--host", "0.0.0.0", "--port", "8000"]