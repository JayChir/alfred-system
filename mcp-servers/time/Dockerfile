FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Expose FastMCP default port
EXPOSE 8000

# Run the server
CMD ["python", "-m", "uvicorn", "src.server:mcp", "--host", "0.0.0.0", "--port", "8000"]