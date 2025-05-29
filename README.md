# Mock Ticketing & Event-Driven Document Processing System

This project provides a comprehensive suite of services including:
1.  A **ServiceNow-like Mock API** for managing incidents and service requests.
2.  An **Event-Driven Serverless Application** that triggers a Kubeflow Pipeline when a PDF is uploaded to a MinIO S3 bucket.
3.  Supporting infrastructure components for **MinIO eventing** and **Knative** deployment on OpenShift.

The system is designed for containerized deployment on OpenShift, leveraging OpenShift Serverless (Knative), OpenShift AI (Kubeflow Pipelines), and Tekton for CI/CD.

## Project Structure

```
.
├── apps
│   ├── api
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── .k8s
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── route.yaml
│   │   └── .tkn
│   │       ├── pipeline.yaml
│   │       ├── pipeline_run.yaml
│   │       ├── buildah-task.yaml
│   │       └── tekton-pvc.yaml
│   └── s3-event-handler
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── .openshift
│       │   ├── knative-service.yaml
│       │   ├── trigger.yaml
│       │   └── rbac.yaml
│       └── .tkn
│           ├── pipeline.yaml
│           ├── pipeline_run.yaml
│           └── tekton-pvc.yaml
├── bootstrap
├── LICENSE
├── pipeline
│   ├── api-to-rag
│   └── pdf-to-docling
│       └── pdf-pipeline.py
├── README.md
└── services
    ├── cloudevents
    │   ├── deployment.yaml
    │   ├── secret.yaml
    │   └── service.yaml
    ├── docling
    ├── instructlab
    ├── knative
    │   └── broker.yaml
    └── minio
        ├── creeate-secret.yaml
        ├── deployment.yaml
        ├── namespace.yaml
        ├── pvc.yaml
        ├── route.yaml
        ├── sa.yaml
        └── service.yaml
```

---

## I. ServiceNow Mock API

A Flask-based REST API that provides mock ServiceNow functionality, focusing on closed ticket data.

### Technical Stack (Mock API)

* Python 3.11
* Flask, Gunicorn
* Red Hat Universal Base Image (UBI) 9

### Features (Mock API)

* **Incident Management:** CRUD operations, filtering, pagination.
* **Service Request Management:** CRUD operations, filtering, pagination.
* **Health Check Endpoint:** `/api/v1/health`.

---

## II. Serverless S3 -> Kubeflow Pipeline Trigger

An event-driven serverless application that:
1.  Listens for PDF file uploads to a MinIO S3 bucket (via Knative Eventing).
2.  Triggers a (stubbed) Kubeflow Pipeline to process the PDF.

### Technical Stack (Serverless Trigger)

* Python 3.11, Flask (for Knative service)
* Kubeflow Pipelines SDK (`kfp`)
* Red Hat Universal Base Image (UBI) 9
* OpenShift Serverless (Knative Serving and Eventing)
* MinIO for S3 storage
* `radiorabe/minio-cloudevents-service` for MinIO to Knative event bridging.

### Kubeflow Pipeline (Stub)

* Located at `pipeline/pdf-to-docling/pdf-pipeline.py`.
* A simple pipeline that accepts S3 bucket/key for a PDF and logs this information.
* This pipeline needs to be compiled (e.g., `python pipeline/pdf-to-docling/pdf-pipeline.py` to produce `simple_pdf_pipeline.tar.gz`) and uploaded to your Kubeflow Pipelines instance on OpenShift AI.

---

## III. Setup and Deployment

This section covers setting up MinIO, the event bridge, Knative components, the Mock API, and the Serverless KFP trigger.

### Prerequisites

* OpenShift Cluster (4.x)
* OpenShift Serverless Operator installed (providing Knative Serving & Eventing).
* OpenShift AI Operator installed (providing Kubeflow Pipelines).
* Tekton Pipelines Operator installed.
* OpenShift CLI (`oc`) installed and configured.
* MinIO Client (`mc`) installed and configured.
* Podman (or Docker) for building container images locally.
* Tekton CLI (`tkn`) (optional).

### Step 1: Deploy MinIO S3 Storage (Example)

(As previously detailed - using `services/minio/` YAMLs. Ensure `create-secret.yaml` is correctly named and populated.)

```
oc apply -f services/minio/namespace.yaml
oc apply -f services/minio/create-secret.yaml
# ... apply other MinIO YAMLs ...
mc alias set osminio $(oc get route minio -n minio -o jsonpath='{.spec.host}') <your-minio-access-key> <your-minio-secret-key>
mc mb osminio/pdf-inbox
```

Step 2: Deploy Knative Eventing Broker
Define your target namespace, e.g., your-serverless-project.


```
# oc new-project your-serverless-project # If it doesn't exist
oc apply -f services/knative/broker.yaml -n your-serverless-project
```

Ensure the namespace in services/knative/broker.yaml is your-serverless-project.


Step 3: Deploy MinIO CloudEvents Bridge Service
```
# Update MINIO_WEBHOOK_SECRET_KEY in services/cloudevents/secret.yaml
oc apply -f services/cloudevents/secret.yaml -n your-serverless-project
oc apply -f services/cloudevents/deployment.yaml -n your-serverless-project
oc apply -f services/cloudevents/service.yaml -n your-serverless-project
```

Ensure K_SINK in services/cloudevents/deployment.yaml points to your Broker and namespace is your-serverless-project.


Step 4: Configure MinIO Bucket Notifications
(As previously detailed - using mc event add to point to the minio-cloudevents-service in your-serverless-project using the shared secret.)

Step 5: Build and Deploy ServiceNow Mock API (Manual or via Tekton)
Manual Build & Push (Example): Navigate to apps/api/

```
podman build -t quay.io/your-quay-username/servicenow-mock-api:latest -f Dockerfile .
podman push quay.io/your-quay-username/servicenow-mock-api:latest
```

Manual Deploy to OpenShift: (Update image path in your apps/api/.k8s/deployment.yaml)

```
oc apply -f apps/api/.k8s/ -n your-api-project # Target namespace for the API
```

Step 6: Build and Deploy Serverless S3 Event Handler (KFP Trigger)
Prepare Kubeflow Pipeline: (Compile and upload pipeline/pdf-to-docling/pdf-pipeline.py as simple_pdf_pipeline.tar.gz to KFP UI).
Build Container Image for s3-event-handler: Navigate to apps/s3-event-handler/

```
podman build -t quay.io/your-quay-username/s3-kfp-trigger:latest -f Dockerfile .
podman push quay.io/your-quay-username/s3-kfp-trigger:latest
```

Deploy to OpenShift Serverless (Manual): (Apply YAMLs from apps/s3-event-handler/.openshift/, ensuring namespaces, image path, KFP_ENDPOINT, and KFP_PIPELINE_NAME are correct.)
```
oc apply -f apps/s3-event-handler/.openshift/rbac.yaml # Ensure correct target namespaces
oc apply -f apps/s3-event-handler/.openshift/knative-service.yaml -n your-serverless-project
oc apply -f apps/s3-event-handler/.openshift/trigger.yaml -n your-serverless-project
```

Step 7: Tekton CI/CD Pipeline for ServiceNow Mock API (OPTIONAL)
The Tekton pipeline (.tkn/pipeline.yaml)

Prerequisites for Tekton:

Ensure Tekton ClusterTasks like git-clone, buildah (or your custom buildah-mock-api task), and kubernetes-actions (or openshift-client) are available on your cluster. If buildah-mock-api is a custom task, apply its definition

```
oc apply -f .tekton/buildah-task.yaml -n your-tekton-pipelines-namespace
```

Create a PVC for Tekton workspaces:

```
oc apply -f .tekton/tekton-pvc.yaml -n your-tekton-pipelines-namespace
```

Create a registry secret (e.g., quay-credentials) for pushing images:


Replace placeholders; use your Tekton pipeline namespace

```
oc create secret docker-registry quay-credentials \
  --docker-server=quay.io \
  --docker-username='<your_quay_robot_username>' \
  --docker-password='<your_quay_robot_token>' \
  --docker-email='unused@example.com' \
  -n your-tekton-pipelines-namespace
```

Ensure this secret is usable by the pipeline ServiceAccount or referenced in the PipelineRun workspace dockerconfig-ws.
Apply the Tekton Pipeline Definition:


```
oc apply -f .tekton/pipeline.yaml -n your-tekton-pipelines-namespace
```

Trigger the Pipeline with Optional Phases:
Modify and apply .tekton/pipeline_run.yaml. You can control execution using the skip-* parameters:

skip-fetch-repository: "true" (to skip git clone)
skip-build-and-push: "true" (to skip image build)
skip-apply-manifests: "true" (to skip deployment)
Example snippet from pipeline_run.yaml:


```
# ...
params:
- name: git-url
  value: "[https://github.com/your-username/your-repo.git](https://github.com/your-username/your-repo.git)"
- name: image-url
  value: "quay.io/your-username/mock-servicenow-api"
- name: context-path # Path to the API code and Dockerfile in your repo
  value: "apps/api"
- name: manifest-dir # Path to the API's k8s manifests in your repo
  value: "apps/api/.k8s"
# --- Skip Control Parameters ---
- name: skip-fetch-repository
  value: "false" # Set to "true" to skip
- name: skip-build-and-push
  value: "false" # Set to "true" to skip
- name: skip-apply-manifests
  value: "false" # Set to "true" to skip
# ... other params ...
```

Apply the configured PipelineRun:



```
oc apply -f .tekton/pipeline_run.yaml -n your-tekton-pipelines-namespace
```
Monitor Pipeline Execution:


```
tkn pipelinerun list -n your-tekton-pipelines-namespace
tkn pipelinerun logs <pipelinerun-name> -f -n your-tekton-pipelines-namespace
```

IV. API Usage & Testing
Mock API Endpoints
Health Check: GET /api/v1/health
Response: {"status": "UP"}
Incidents:
List: GET /api/v1/incidents (Query params: limit, offset, state, priority, etc.)
Get single: GET /api/v1/incidents/<incident_number>
Create: POST /api/v1/incidents (Body: {"short_description": "...", "caller_id": "...", "description": "..."})
Update: PUT/PATCH /api/v1/incidents/<incident_number>
Service Requests: Similar CRUD endpoints under /api/v1/requests.
Example curl for Mock API (replace <mock-api-route>):


```
curl http://<mock-api-route-for-your-api-project>/api/v1/incidents?limit=2 | jq
```

Testing Event-Driven Flow
Upload a PDF file to the configured MinIO bucket (e.g., pdf-inbox).

Monitor Logs:
minio-cloudevents-service (in your-serverless-project): 
```
oc logs -l app=minio-cloudevents-service -n your-serverless-project -f
```
s3-event-handler Knative service (e.g., kfp-s3-trigger in your-serverless-project): 
```
oc logs -l serving.knative.dev/service=kfp-s3-trigger -c user-container -n your-serverless-project -f
```
Check Kubeflow Pipelines UI: A new run for your PDF processing pipeline should appear.

License
This project is licensed under the MIT License - see the LICENSE file for details.