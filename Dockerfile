FROM python:3.11-slim

WORKDIR /app

# Install Node.js + npm for mcp-remote (npx)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Pre-install mcp-remote globally so npx doesn't download it at runtime
RUN npm install -g mcp-remote

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
