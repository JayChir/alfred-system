FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Expose Sequential Thinking MCP port
EXPOSE 8007

# Run the server
CMD ["python", "src/server.py"]