# Use a standard Python slim image as the base
FROM python:3.9-slim

# Install system dependencies that might be needed by Python libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install all necessary Python libraries for both pipeline components
# This creates a single, reusable image for the entire pipeline.
RUN pip install --no-cache-dir \
    requests==2.28.1 \
    pymilvus==2.3.1 \
    sentence-transformers==2.2.2 \
    torch==1.13.1 --extra-index-url https://download.pytorch.org/whl/cpu

# Set a working directory
WORKDIR /app