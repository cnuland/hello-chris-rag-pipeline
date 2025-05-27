# ServiceNow Mock API - Incident and Request Management

A Flask-based REST API that provides mock ServiceNow functionality for incident and service request management, with a focus on closed ticket retrieval and management. This project includes containerization using Podman and automated deployment using Tekton pipelines and Kubernetes.

## Technical Stack

- Python 3.11
- Flask web framework
- Gunicorn WSGI server
- Red Hat Universal Base Image (UBI)
- Podman for container management
- Kubernetes for deployment
- Tekton for CI/CD pipelines

## Features

- **Incident Management**
  - Create, read, update, and delete incidents
  - Filter by state, priority, assignment group, and more
  - Pagination support for large result sets
  - Automatic handling of closed ticket metadata

- **Service Request Management**
  - Full CRUD operations for service requests
  - State and stage tracking
  - Support for item details and approvals
  - Closed request handling

- **Health Check Endpoint**
  - Monitor API availability
  - Simple status check endpoint

## Setup and Installation

### Local Development

1. Set up Python environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r app/requirements.txt
   ```

2. Run the development server:
   ```bash
   python app.py
   ```

### Container Development with Podman

1. Build the container image:
   ```bash
   podman build -t servicenow-mock-api .
   ```

2. Run the container locally:
   ```bash
   podman run -d -p 8080:8080 servicenow-mock-api
   ```

3. Managing containers:
   ```bash
   podman ps                    # List running containers
   podman stop <container-id>   # Stop a container
   podman rm <container-id>     # Remove a container
   ```

### Tekton Pipeline Deployment

1. Apply the Tekton pipeline configuration:
   ```bash
   kubectl apply -f .tkn/
   ```

2. Monitor pipeline execution:
   ```bash
   tkn pipeline list
   tkn pipelinerun list
   ```

### Kubernetes Deployment

1. Apply the Kubernetes manifests:
   ```bash
   kubectl apply -f .k8s/
   ```

2. Verify deployment:
   ```bash
   kubectl get pods
   kubectl get services
   kubectl get routes
   ```

## API Documentation

### Endpoints

#### Health Check
```
GET /api/v1/health
Response: {"status": "UP"}
```

#### Incidents

- List incidents:
  ```
  GET /api/v1/incidents
  Query parameters:
  - limit (default: 10)
  - offset (default: 0)
  - state
  - priority
  - assignment_group
  - caller_id
  - cmdb_ci
  - category
  ```

- Get single incident:
  ```
  GET /api/v1/incidents/<incident_number>
  ```

- Create incident:
  ```
  POST /api/v1/incidents
  Required fields:
  - short_description
  - caller_id
  - description
  ```

- Update incident:
  ```
  PUT/PATCH /api/v1/incidents/<incident_number>
  ```

#### Service Requests

- List requests:
  ```
  GET /api/v1/requests
  Query parameters:
  - limit (default: 10)
  - offset (default: 0)
  - state
  - requested_by
  - assignment_group
  ```

- Get single request:
  ```
  GET /api/v1/requests/<request_number>
  ```

- Create request:
  ```
  POST /api/v1/requests
  Required fields:
  - short_description
  - requested_for
  ```

### Response Format

All responses follow this structure:
```json
{
    "result": [
        {
            "number": "INC001001",
            "state": "Closed",
            "short_description": "Email server unresponsive",
            ...
        }
    ],
    "total_records": 100,
    "limit": 10,
    "offset": 0
}
```

## CI/CD Pipeline

The project uses Tekton pipelines for continuous integration and deployment:

1. Pipeline Tasks:
   - Source code checkout
   - Build container image using Podman
   - Run tests
   - Deploy to Kubernetes

2. Pipeline Resources:
   - Git repository
   - Container image registry
   - Kubernetes deployment manifests

3. Triggering Builds:
   - Automatically on git push
   - Manually using tkn CLI

## Troubleshooting

### Podman Issues

1. Image build failures:
   - Verify UBI registry access
   - Check network connectivity
   - Ensure proper SELinux context

2. Container runtime issues:
   - Check port conflicts
   - Verify container logs
   - Monitor resource usage

### Pipeline Debugging

1. Check pipeline logs:
   ```bash
   tkn pipelinerun logs <pipelinerun-name>
   ```

2. Verify task status:
   ```bash
   tkn taskrun list
   ```

### Kubernetes Deployment

1. Pod issues:
   ```bash
   kubectl describe pod <pod-name>
   kubectl logs <pod-name>
   ```

2. Service connectivity:
   ```bash
   kubectl get endpoints
   kubectl describe service servicenow-mock-api
   ```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

