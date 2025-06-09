FROM python:3.11-slim-bookworm

ENV GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no"

# Install system-level dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl wget git procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install required Python packages: docling, boto3 (for S3 access), and torch for CPU
# We are installing boto3 in the same layer as docling for efficiency.
RUN pip install --no-cache-dir \
    docling \
    boto3 \
    --extra-index-url https://download.pytorch.org/whl/cpu

# Set home directories for HuggingFace and Torch to avoid writing to user's home
ENV HF_HOME=/tmp/huggingface
ENV TORCH_HOME=/tmp/torch

# Pre-download models to include them in the image, avoiding downloads at runtime
COPY docs/examples/minimal.py /root/minimal.py
RUN docling-tools models download

# On container environments, always set a thread budget to avoid undesired thread congestion.
ENV OMP_NUM_THREADS=4

# Example of how to run a minimal test script inside the container:
# > cd /root/
# > python minimal.py

# Running as `docker run -e DOCLING_ARTIFACTS_PATH=/root/.cache/docling/models` will use the
# model weights included in the container image.