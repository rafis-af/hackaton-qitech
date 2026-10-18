FROM python:3.11-slim AS base

RUN adduser --system --no-create-home user

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt /requirements.txt
RUN pip install --upgrade pip && pip install -r /requirements.txt

WORKDIR /app
COPY src /app

FROM base AS api
RUN chown -R user /app
USER user

CMD uvicorn app:app --host 0.0.0.0 --port 3000 --no-server-header

FROM base AS tests
COPY requirements-dev.txt /requirements-dev.txt
RUN pip install -r /requirements-dev.txt

WORKDIR /work
COPY database /work/database
COPY tests /work/tests

CMD ["pytest"]
