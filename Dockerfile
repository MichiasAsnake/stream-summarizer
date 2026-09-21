FROM python:3.11-slim
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install -U pip && pip install -e ".[llm-gemini]"
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
