# Use the specified Red Hat Universal Base Image for Python 3.11
FROM registry.redhat.io/ubi9/python-311@sha256:82a16d7c4da926081c0a4cc72a84d5ce37859b50a371d2f9364313f66b89adf7

USER 0

# Set the working directory in the container
WORKDIR /opt/app-root/src

# Copy the requirements file and install dependencies
COPY ./app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY ./app/ .

# Expose the port the app runs on
EXPOSE 8080

# Command to run the application using gunicorn
# Gunicorn is a more production-ready WSGI server compared to Flask's built-in server
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]