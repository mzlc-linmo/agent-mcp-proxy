FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chmod=644 proxy.py policy.py admin.py admin_auth.py admin_config.py lifecycle.py endpoint_manager.py catalog.py catalog_runtime.py service_transport.py service_import.py runtime.py ./
COPY --chmod=644 static/ ./static/
RUN chmod 755 /app /app/static
USER 65534:65534
CMD ["uvicorn", "runtime:create_runtime", "--factory", "--host", "0.0.0.0", "--port", "8082", "--no-access-log", "--no-proxy-headers"]
