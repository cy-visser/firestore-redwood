# The event parser is shared with the CDC service rather than duplicated, so
# the build context is the repository root and two directories are copied in.
# Firestore's protojson encoding is fiddly enough that a second copy of the
# decoder would eventually disagree with the first.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

COPY agent_bridge/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_bridge/main.py ./main.py
COPY cdc_service/firestore_event.py ./firestore_event.py

EXPOSE 8080

# One worker with several threads, matching the CDC service. The resolved
# agent engine handle is cached per process, so extra processes would each pay
# the lookup and hold their own copy for no benefit. Requests here are almost
# entirely spent waiting on Vertex AI, so threads are the right axis.
#
# The timeout is generous because a cold agent has to start up before it can
# answer, and a timeout here would return 500 and make Eventarc redeliver an
# event the agent is in fact still working on.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 main:app
