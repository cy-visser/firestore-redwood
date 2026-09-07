FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy A2A Multi-Agent platform application files
COPY loyalty_agent/ ./loyalty_agent/

# Expose container health check and A2A discovery port
EXPOSE 8080

# Run Loyalty Agent persistent event listener daemon
ENTRYPOINT ["python", "-m", "loyalty_agent.main", "--daemon"]
