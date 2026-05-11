# Multi-stage build for optimized image size
FROM python:3.12-slim as builder

WORKDIR /app

# Install dependencies in virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# Final stage - minimal runtime image
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY app.py .
COPY gcp_asset_service.py .
COPY aws_service.py .
COPY cache_manager.py .

# Create .streamlit directory for config
RUN mkdir -p ~/.streamlit

# Create Streamlit config file
RUN echo "\
[client]\n\
headless = true\n\
port = 8501\n\
\n\
[server]\n\
enableCORS = false\n\
enableXsrfProtection = true\n\
maxUploadSize = 200\n\
" > ~/.streamlit/config.toml

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8501', timeout=5)"

# Expose port
EXPOSE 8501

# Run Streamlit app
CMD ["streamlit", "run", "app.py", "--server.runOnSave", "false", "--logger.level=info"]
