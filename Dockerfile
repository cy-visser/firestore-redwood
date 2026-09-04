FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY firestore_auth.py ./
COPY retail_catalog.py ./
COPY loyalty_agent/ ./loyalty_agent/

# Expose container health check port
EXPOSE 8080

# Run Loyalty Agent persistent event listener daemon
ENTRYPOINT ["python", "-m", "loyalty_agent.main", "--daemon"]
