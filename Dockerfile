FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy A2A Multi-Agent platform and bridge files
COPY loyalty_agent/ ./loyalty_agent/
COPY scripts/run_firestore_agent_bridge.py ./scripts/run_firestore_agent_bridge.py
COPY deployed_native_agents.json ./deployed_native_agents.json

# Expose container health check port
EXPOSE 8080

# Run Redwood Retail Firestore-to-Agent-Runtime Event Bridge
ENTRYPOINT ["python", "scripts/run_firestore_agent_bridge.py"]
