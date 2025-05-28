# Use the specified Red Hat Universal Base Image for Python 3.11
FROM registry.redhat.io/ubi9/python-311@sha256:82a16d7c4da926081c0a4cc72a84d5ce37859b50a371d2f9364313f66b89adf7

USER 0

WORKDIR /opt/app-root/src

# Install OS packages if needed by KFP SDK or other dependencies (e.g., gcc for some pip installs)
# RUN microdnf install -y gcc python3-devel && microdnf clean all

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
# If you were to compile KFP pipelines within the serverless app (not recommended for this flow)
# you would also copy the kfp_pipelines directory here.
# COPY kfp_pipelines/ kfp_pipelines/

EXPOSE 8080

# Standard OpenShift non-root user
USER 1001

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]