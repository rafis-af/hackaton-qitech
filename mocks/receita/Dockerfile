FROM python:3.11-slim

RUN adduser --system --no-create-home user
RUN pip install --no-cache-dir fastapi==0.141.1 "uvicorn[standard]==0.52.3"

WORKDIR /app
COPY app.py /app/app.py

USER user
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
