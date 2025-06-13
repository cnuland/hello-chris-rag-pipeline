# Use a standard Python slim image as the base
FROM python:3.9-slim

# Install system dependencies that might be needed by Python libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python libraries
RUN pip install --no-cache-dir \
    torch==1.13.1 --extra-index-url https://download.pytorch.org/whl/cpu \
    transformers==4.30.2 \
    huggingface_hub==0.15.1 \
    sentence-transformers==2.2.2 \
    pymilvus==2.3.1 \
    requests==2.28.1 \
    marshmallow==3.19.0 \
    environs==9.5.0 \
    "numpy<2.0.0"

# Set a working directory
WORKDIR /app
