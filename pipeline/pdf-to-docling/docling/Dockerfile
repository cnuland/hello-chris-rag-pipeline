FROM python:3.11-slim-bookworm

ENV GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no"

# Install system-level dependencies required by the Python libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl wget git procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install all necessary Python packages in a single layer for efficiency.
# This image will be used by all components in the PDF-to-Milvus pipeline.
RUN pip install --no-cache-dir \
    docling \
    boto3 \
    pymilvus==2.3.1 \
    sentence-transformers==2.2.2 \
    torch==1.13.1 \
    --extra-index-url https://download.pytorch.org/whl/cpu

# Set home directories for HuggingFace and Torch to avoid writing to user's home
# inside the container, which can cause permission issues on some platforms.
ENV HF_HOME=/tmp/huggingface
ENV TORCH_HOME=/tmp/torch

# Pre-download models to include them in the image, avoiding downloads at runtime
# Note: This only downloads models for docling itself. The sentence-transformer
# model will be downloaded on first use inside the KFP component.
COPY docs/examples/minimal.py /root/minimal.py
RUN docling-tools models download

# On container environments, always set a thread budget to avoid undesired thread congestion.
ENV OMP_NUM_THREADS=4