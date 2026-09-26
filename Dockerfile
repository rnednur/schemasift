FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]'
COPY schemasift.example.yaml /app/schemasift.yaml
EXPOSE 8080
CMD ["schemasift", "--config", "/app/schemasift.yaml", "serve", "--host", "0.0.0.0"]

