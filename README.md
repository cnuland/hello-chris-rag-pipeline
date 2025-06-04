# Mock Ticketing & Event-Driven Document Processing System on OpenShift

This project deploys a multifaceted system on OpenShift, featuring:
1.  A **ServiceNow-like Mock API** for simulating incident and service request management.
2.  An **Event-Driven Serverless PDF Processing Pipeline**: When a PDF is uploaded to a MinIO S3 bucket, a MinIO webhook triggers a custom bridge service (`minio-event-bridge`). This bridge service creates a CloudEvent and sends it to an Apache Kafka-backed Knative Broker. A Knative Trigger then forwards this event to a serverless function (`s3-event-handler`) which ultimately launches a Kubeflow Pipeline for processing.
3.  Supporting infrastructure including **MinIO**, **Apache Kafka** (for the Knative Broker), and **Knative** components, with **Istio** (via OpenShift Service Mesh) as the Knative ingress.

The entire system is designed for containerized deployment and is managed via **GitOps principles using ArgoCD and Kustomize**. It leverages key OpenShift Operators for robust functionality, demonstrating a cloud-native, event-driven architecture suitable for scalable data ingestion and processing workflows.

## Overall Architecture

The system is architecturally divided into an event-driven data pipeline and a standalone mock API, orchestrated and managed using GitOps.

### 1. Event-Driven PDF Processing Workflow

This workflow is initiated by a PDF upload and culminates in a Kubeflow Pipeline execution:

* **MinIO (S3 Storage):** Stores PDF files. Deployed on OpenShift, preferably using OpenShift Data Foundation (ODF/OCS) for persistent storage.
* **MinIO Bucket Notification (Webhook):** Upon PDF upload, MinIO is configured (via server-side environment variables in its Deployment YAML and linked to a specific bucket using the `mc event add` command) to send an HTTP POST webhook notification.
* **`minio-event-bridge` (Knative Service):** A custom Python Flask application (`apps/minio-event-bridge/app.py`) deployed as a Knative Service. It receives the raw JSON webhook from MinIO, transforms this payload into a structured CloudEvent (e.g., setting `type` to `s3:ObjectCreated:Put` and `source` to `minio:s3`), and forwards it to the Knative Broker. This service runs within the Istio service mesh.
* **Knative Eventing Broker (Kafka-backed):** The `minio-event-bridge` forwards the newly created CloudEvent (via HTTP POST) to a Kafka-backed Knative Broker (e.g., `kafka-broker`). This broker provides content-based routing and decouples event producers from event consumers. The broker itself uses Kafka topics for its data plane, managed by the OpenShift Streams Operator.
* **Knative Trigger:** Subscribes to specific CloudEvents from the `kafka-broker` (e.g., filtering for `type: "s3:ObjectCreated:Put"` and `source: "minio:s3"` as produced by the `minio-event-bridge`).
* **`s3-event-handler` (Knative Service):** A Python Flask application (`apps/s3-event-handler/app.py`) deployed as a serverless function using Knative Serving. It's the subscriber for the Knative Trigger. Upon receiving a CloudEvent, it parses the `data` payload (which contains the original MinIO event details) to extract PDF metadata (bucket, key). This service also runs within the Istio service mesh.
* **Kubeflow Pipelines (via OpenShift AI Operator):** The `s3-event-handler` uses the Kubeflow Pipelines (KFP) SDK to trigger a specific pipeline (e.g., `pdf-to-docling`) in Kubeflow, passing the PDF information for further processing. The KFP orchestrates the actual PDF processing tasks, which might involve GPU-accelerated steps.
* **Istio (via OpenShift Service Mesh Operator):** Functions as the ingress controller for Knative Serving. It manages external and internal traffic to Knative Services like `minio-event-bridge` and `s3-event-handler` through Istio Gateways and VirtualServices. The namespace hosting these Knative Services (`rag-pipeline-workshop`) must be part of the Service Mesh.
* **Apache Kafka (via OpenShift Streams Operator):** Kafka is used as the backing mechanism for the Knative Kafka-backed Broker, providing persistence and scalability for the event bus.

### 2. ServiceNow Mock API

* An independent Flask/Gunicorn REST API providing mock ticketing data for incidents and service requests.
* Deployed as a standard OpenShift application (Deployment, Service, Route).
* An optional Tekton CI/CD pipeline is provided for building and deploying this API.

*(For a visual representation, please refer to the PlantUML diagram in the `plantuml_architecture_diagram_v2` artifact, which can be rendered in tools like draw.io or directly via PlantUML.)*

## Required OpenShift Operators

Successful deployment and operation of this project depend on the following OpenShift Operators being installed and configured:

* **OpenShift AI Operator:** Essential for deploying and managing Kubeflow Pipelines, which orchestrates the PDF processing tasks. Also provides environments like JupyterHub for development.
* **Nvidia GPU Operator:** (Conditional) Required if any Kubeflow Pipeline components or other workloads need GPU acceleration. Ensures GPU resources are available and managed.
* **Node Feature Operator (NFO):** Often a prerequisite or companion to the GPU Operator. NFO discovers and labels hardware features on cluster nodes, enabling GPU-aware scheduling.
* **OpenShift Serverless Operator:** Core to the event-driven architecture. It installs and manages:
    * **Knative Serving:** For deploying and running serverless applications like the `minio-event-bridge` and `s3-event-handler`.
    * **Knative Eventing:** For managing the event flow, including Brokers and Triggers.
* **OpenShift Service Mesh Operator:** Installs and manages Istio. In this setup, Istio is configured as the ingress controller for Knative Serving, handling traffic routing to serverless applications. It also enables mesh capabilities like mTLS and traffic policies if configured. The application namespace (`rag-pipeline-workshop`) must be added to the `ServiceMeshMemberRoll`.
* **OpenShift GitOps Operator:** Installs and manages ArgoCD. This is fundamental for the GitOps workflow, automating the deployment and lifecycle management of all applications and services defined in this Git repository.
* **OpenShift Streams Operator (AMQ Streams):** Deploys and manages Apache Kafka clusters on OpenShift. Kafka is used here as the backing store for the Knative Kafka-backed Broker.
* **OpenShift Data Foundation (ODF/OCS):** (Recommended for Production) Provides enterprise-grade persistent, replicated storage. Crucial for stateful services like MinIO (for object storage), Apache Kafka (for message logs), and Kubeflow Pipelines (for artifacts and metadata). For development/PoC, ephemeral storage or standard PVCs with a basic `StorageClass` can be used, but ODF is recommended for resilience and scalability.

## GitOps with ArgoCD

This entire project is designed to be managed using a GitOps workflow powered by ArgoCD (installed via the OpenShift GitOps Operator).
* **Kustomize:** Each component's Kubernetes/OpenShift manifests are organized into directories with `kustomization.yaml` files (e.g., in `apps/api/.k8s/`, `services/minio/`, `services/kafka/`, `services/knative/`, `apps/s3-event-handler/.openshift/`). This allows for base configurations and environment-specific overlays.
* **App of Apps Pattern:** A root ArgoCD Application (defined in `bootstrap/app-of-apps.yaml`) manages a set of child ArgoCD Applications. Each child application (defined in `bootstrap/`) points to a Kustomize configuration for a specific component (e.g., MinIO, Kafka, Knative Broker, Mock API, `minio-event-bridge`, `s3-event-handler`).
* **Declarative State:** The Git repository is the single source of truth. All desired states of the applications and infrastructure components are declared in YAML. ArgoCD continuously monitors the Git repository and applies changes to the cluster to match this desired state. This ensures consistency, auditability, and automated rollbacks/rollouts.

## Project Structure
```
.
├── apps
│   ├── api                     # ServiceNow Mock API (Flask app, Dockerfile, requirements)
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │   └── .k8s/               # (Assumed) Kubernetes manifests for the API, managed by ArgoCD app-mock-api.yaml
│   │       └── kustomization.yaml # (And deployment.yaml, service.yaml, route.yaml)
│   ├── minio-event-bridge      # Custom Python Flask app to bridge MinIO webhooks to Knative Broker
│   │   ├── app.py              # Contains the Flask webhook receiver and CloudEvent creation logic
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │   └── .openshift/         # (Assumed) Knative Service definition for this bridge, managed by app-event-bridge.yaml
│   │       └── kustomization.yaml # (And minio-event-bridge-ksvc.yaml)
│   └── s3-event-handler        # Serverless KFP Trigger (Knative Service)
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       └── .openshift/         # Knative Service, RBAC YAMLs for KFP trigger app, managed by app-event-handler.yaml
│           ├── knative-service.yaml # Defines 'kfp-s3-trigger' ksvc
│           ├── kustomization.yaml
│           └── rbac.yaml
├── bootstrap                   # ArgoCD Application definitions
│   ├── app-event-bridge.yaml   # ArgoCD app for the 'minio-event-bridge' service
│   ├── app-event-handler.yaml  # ArgoCD app for 's3-event-handler' Knative service & its Trigger
│   ├── app-kafka.yaml          # ArgoCD app for Kafka cluster & topic
│   ├── app-knative-broker.yaml # ArgoCD app for Knative Kafka-backed Broker & its Trigger
│   ├── app-minio.yaml          # ArgoCD app for MinIO deployment
│   ├── app-mock-api.yaml       # ArgoCD app for ServiceNow Mock API
│   ├── app-of-apps.yaml        # Root ArgoCD application managing all other apps
│   └── kustomization.yaml      # Kustomization for all bootstrap ArgoCD applications
├── LICENSE
├── pipeline
│   └── pdf-to-docling          # Kubeflow Pipeline definition
│       ├── pdf-pipeline.py     # KFP SDK Python code for the pipeline
│       ├── simple_pdf_pipeline.yaml # Compiled KFP pipeline (output of pdf-pipeline.py)
│       └── test2.pdf           # Example PDF for testing
├── README.md
├── scripts
│   ├── example.env             # Consolidated example environment variables
│   ├── generate_and_upload_pdf.py # Script to create a test PDF and upload to MinIO
│   └── setup_minio_events.sh   # Script to configure MinIO bucket notifications (now for webhook to bridge)
└── services                    # Kubernetes/OpenShift manifests for backing services
    ├── kafka                   # Kafka cluster (Strimzi) and topic definitions
    │   ├── kafka.yaml          # Defines 'my-kafka-cluster'
    │   ├── kustomization.yaml
    │   └── topic.yaml          # Defines topics used by the Knative Kafka Broker
    ├── knative                 # Knative Eventing components (Broker, ConfigMaps, Trigger)
    │   ├── base/               # For global Knative configs (e.g., in knative-eventing ns)
    │   │   ├── config.yaml     # e.g., config-br-defaults ConfigMap
    │   │   └── kustomization.yaml
    │   ├── broker.yaml         # Defines 'kafka-broker' (Kafka-backed) in app namespace
    │   ├── config.yaml         # ConfigMap for the 'kafka-broker' (e.g., kafka-broker-config.yaml)
    │   ├── kustomization.yaml  # Bundles broker.yaml, config.yaml, and trigger.yaml
    │   ├── source.yaml         # Knative KafkaSource - NO LONGER USED for MinIO events with the bridge.
    │                           # This file can be removed if not used for other purposes.
    │   └── trigger.yaml        # Defines Knative Trigger for s3-event-handler, subscribes to kafka-broker.
    └── minio                   # MinIO S3 Storage deployment
        ├── create-secret.yaml
        ├── deployment.yaml     # This MUST contain MINIO_NOTIFY_WEBHOOK_* env vars pointing to minio-event-bridge
        ├── kustomization.yaml
        ├── namespace.yaml
        ├── network-policies.yaml # NetworkPolicies for MinIO ingress/egress
        ├── pvc.yaml
        ├── route.yaml
        ├── sa.yaml
        └── service.yaml
```
*(Note: Application-specific manifests like Knative Services for `minio-event-bridge` and `s3-event-handler` are typically located in their respective `apps/.../.openshift/` directories and managed by dedicated ArgoCD applications defined in `bootstrap/`. The `services/knative/trigger.yaml` defines the trigger linking the `kafka-broker` to the `s3-event-handler`.)*

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
1. Listens for PDF file uploads to a MinIO S3 bucket. MinIO sends a webhook.
2. A custom **`minio-event-bridge`** service (Python Flask app deployed as a Knative Service) receives this webhook, transforms the MinIO JSON payload into a CloudEvent, and forwards it to a Knative Broker.
3. A Knative Trigger subscribes to this Broker, filters for relevant CloudEvents, and invokes the `s3-event-handler`.
4. The **`s3-event-handler`** (Python Flask app deployed as a Knative Service) parses the CloudEvent data and triggers a Kubeflow Pipeline to process the PDF.

### Technical Stack (Event-Driven Workflow)
* MinIO for S3 storage
* Custom Python Flask app (`minio-event-bridge`) for MinIO webhook to CloudEvent transformation.
* Apache Kafka (via OpenShift Streams) as the backing mechanism for the Knative Broker.
* OpenShift Serverless (Knative Serving for `minio-event-bridge` & `s3-event-handler`; Knative Eventing for `Broker` & `Trigger`).
* Istio (via OpenShift Service Mesh) as the ingress for Knative Serving.
* Python 3.11, Flask for the serverless functions.
* Kubeflow Pipelines SDK (`kfp`) used by `s3-event-handler`.
* Red Hat Universal Base Image (UBI) 9 for containers.

### Knative Service Networking with Istio (Simplified Flow)
1. External HTTPS request hits an OpenShift Route (e.g., `kfp-s3-trigger-rag-pipeline-workshop.apps...` or `minio-event-bridge-rag-pipeline-workshop.apps...`).
2. The OpenShift Route (often with edge or passthrough termination) directs traffic to the Istio Ingress Gateway service (e.g., in `istio-system`).
3. The Istio Ingress Gateway pod (selected by its service) uses Istio `Gateway` and `VirtualService` resources to route the request.
4. The `VirtualService` (created by Knative's `net-istio-controller` for the `ksvc`) matches the host and path, routing traffic to the Knative service's internal Kubernetes service (e.g., `kfp-s3-trigger.rag-pipeline-workshop` on a port like 80).
5. This internal service routes to the `queue-proxy` sidecar in the application pod (typically on port 8012).
6. The `queue-proxy` forwards the request to your application container (`user-container`) on its specified port (e.g., 8080).

### Kubeflow Pipeline (Stub)
* Located at `pipeline/pdf-to-docling/pdf-pipeline.py`.
* A simple pipeline that accepts S3 bucket/key for a PDF and logs this information.
* The compiled pipeline (e.g., `simple_pdf_pipeline.yaml` or `.tar.gz` from `pipeline/pdf-to-docling/`) needs to be uploaded to your Kubeflow Pipelines instance on OpenShift AI.

---

## III. Setup and Deployment (GitOps with ArgoCD)

This project is designed to be deployed and managed using ArgoCD.

### Prerequisites
* OpenShift Cluster (4.x).
* **Required Operators Installed (see "Required OpenShift Operators" section above).** Ensure they are healthy and configured.
* Git repository hosting this project structure.
* OpenShift CLI (`oc`) installed locally.
* MinIO Client (`mc`) installed locally for initial bucket/event setup.
* (Optional) Podman/Docker for building images if not using pre-built ones.
* (Optional) Tekton CLI (`tkn`).

### Initial Setup Steps:

1.  **Clone Repository & Customize Placeholders:**
    * Clone this Git repository.
    * Review all YAML files (especially in `bootstrap/`, `services/`, and application-specific manifest directories like `apps/api/.k8s/`, `apps/s3-event-handler/.openshift/`, and `apps/minio-event-bridge/.openshift/`) for placeholder values. Update `YOUR_GIT_REPO_URL`, namespaces (e.g., `rag-pipeline-workshop`, `your-api-project`, `kafka`, `minio`, `your-tekton-pipelines-namespace`, `istio-system`, `knative-eventing`), image URLs, and secrets to match your environment.
    * Pay special attention to `services/minio/create-secret.yaml` for MinIO credentials and `services/minio/deployment.yaml` for `MINIO_NOTIFY_WEBHOOK_*` environment variables (ensure the endpoint points to your `minio-event-bridge` service, e.g., `http://minio-event-bridge.rag-pipeline-workshop.svc.cluster.local:8080/webhook`, and the ID like `HTTPBRIDGE` or `RAG` is consistent).
    * Ensure the `BROKER_URL` environment variable in the `minio-event-bridge` service deployment (e.g., in `apps/minio-event-bridge/.openshift/knative-service.yaml`) points to your `kafka-broker`'s ingress (e.g., `http://kafka-broker-ingress.knative-eventing.svc.cluster.local/rag-pipeline-workshop/kafka-broker`).
    * Update `scripts/example.env` with your specific values. You can `source scripts/example.env` to set them in your shell before running scripts.

2.  **Configure Namespace for Istio Service Mesh (Critical for Knative Services):**
    The `rag-pipeline-workshop` namespace (or your chosen serverless namespace where `minio-event-bridge` and `s3-event-handler` run) must be part of your Istio Service Mesh.
    * **Identify your Istio control plane namespace** (usually `istio-system`).
    * **Add Application Namespace to `ServiceMeshMemberRoll`:**
```
# Check current SMMR in your Istio control plane namespace (e.g., istio-system)
oc get smmr default -n istio-system -o yaml
```
        If `rag-pipeline-workshop` is not in `spec.members`:
```
# Edit the ServiceMeshMemberRoll to add your namespace
oc edit smmr default -n istio-system
```
        Add `rag-pipeline-workshop` to the `spec.members` list. Also include `minio` if MinIO pods are to be part of the mesh for direct webhook sending (though if MinIO is outside the mesh, ensure its egress to the bridge is allowed by NetworkPolicies).
```
# Example SMMR spec:
spec:
  members:
    - knative-serving                         # Keep existing members
    - redhat-ods-applications-auth-provider # Keep existing members
    - rag-pipeline-workshop                 # <-- ADD YOUR APPLICATION NAMESPACE
    # - minio                               # Optional: if MinIO itself needs to be in the mesh
```


NOTE: We don't need the above if adding this for each namesapce

```
apiVersion: maistra.io/v1
kind: ServiceMeshMember
metadata:
  name: default
  namespace: rag-pipeline-workshop
spec:
  controlPlaneRef:
    name: data-science-smcp
    namespace: istio-system
```
    * **Ensure Knative Service YAMLs request Istio integration and sidecar injection:**
        Verify that Knative Service definitions for `minio-event-bridge` and `s3-event-handler` include in their `metadata.annotations`:
        `networking.knative.dev/ingress.class: "istio.ingress.networking.knative.dev"`
        And in `spec.template.metadata.annotations`:
        `sidecar.istio.io/inject: "true"`
    *Allow a minute or two for the Service Mesh Operator to reconcile.*
also add    
```
oc patch smmr default -n istio-system --type='json' -p='[{"op": "add", "path": "/spec/members/-", "value": "knative-eventing"}]'
```
3.  **Push Changes to Your Git Repository:** Commit all customized files.

4.  **Apply the Root ArgoCD Application:**
```
# Apply to the namespace where ArgoCD is running (typically 'openshift-gitops')
oc apply -f bootstrap/app-of-apps.yaml -n openshift-gitops
```
    Monitor ArgoCD for successful synchronization of all child applications.

5.  **Post-Deployment MinIO Bucket Notification Setup (Using the Webhook Target):**
    Once MinIO is running (deployed by ArgoCD) with its Webhook notification environment variables active:
    * **Run the `setup_minio_events.sh` script:**
```
# Ensure environment variables from scripts/example.env are set and sourced.
# WEBHOOK_TARGET_ID_IN_MINIO in the script MUST match the ID used in MinIO's env vars (e.g., "HTTPBRIDGE" or "RAG").
cd scripts/
chmod +x setup_minio_events.sh
./setup_minio_events.sh
cd ..
```

6.  **Verify Knative `Trigger` for `minio-event-bridge` Events:**
    The `Trigger` (e.g., `minio-pdf-event-trigger`, likely defined in `services/knative/trigger.yaml` and managed by an ArgoCD app) should filter for CloudEvents produced by your `minio-event-bridge` (e.g., `type: "s3:ObjectCreated:Put"`, `source: "minio:s3"`).

7.  **Prepare and Upload Kubeflow Pipeline:**
    (Compile `pipeline/pdf-to-docling/pdf-pipeline.py` to `simple_pdf_pipeline.yaml` or `.tar.gz` and upload to KFP UI. Match `KFP_PIPELINE_NAME` in `s3-event-handler`'s Knative Service definition.)

### Tekton CI/CD Pipeline (Optional, for Mock API)
The `.tekton/` directory contains resources for an optional Tekton pipeline to build and deploy the ServiceNow Mock API. These resources are managed by the `bootstrap/app-mock-api-tekton.yaml` ArgoCD application.
* The Tekton pipeline (`.tekton/pipeline.yaml`) is designed with optional phases (fetch, build, deploy) controlled by parameters in the `PipelineRun`.
* **Prerequisites:** Ensure Tekton `ClusterTask`s (`git-clone`, `buildah`), and your custom `buildah-task.yaml` (if not using a catalog version) are available. The `.tekton/tekton-pvc.yaml` will be created by ArgoCD. Create a registry secret (e.g., `quay-credentials`) in your Tekton namespace (e.g., `your-tekton-pipelines-namespace`).
* **Usage:** Modify and apply `.tekton/pipeline_run.yaml` to trigger, setting skip parameters as needed.

---

## IV. API Usage & Testing

### Mock API Endpoints

* **Health Check:** `GET /api/v1/health`
    * Response: `{"status": "UP"}`
* **Incidents:**
    * List: `GET /api/v1/incidents` (Query params: `limit`, `offset`, `state`, `priority`, etc.)
    * Get single: `GET /api/v1/incidents/<incident_number>`
    * Create: `POST /api/v1/incidents` (Body: `{"short_description": "...", "caller_id": "...", "description": "..."}`)
    * Update: `PUT/PATCH /api/v1/incidents/<incident_number>`
* **Service Requests:** Similar CRUD endpoints under `/api/v1/requests`.

**Example `curl` (replace `<mock-api-route>` with actual route URL from `oc get route -n your-api-project`):**
```
curl http://<mock-api-route>/api/v1/incidents?limit=2 | jq
```

### Testing Event-Driven Flow (Updated for Webhook Bridge)

1.  **Verify Knative Services are `READY True True True`**:
```
oc get ksvc minio-event-bridge -n rag-pipeline-workshop
oc get ksvc kfp-s3-trigger -n rag-pipeline-workshop
```
    Test their `/health` or `/healthz` endpoints via their OpenShift Routes (get URLs from `oc get ksvc ...`).

2.  **Upload a PDF file** to the MinIO bucket (e.g., `pdf-inbox` on your `osminio` alias) using `mc cp` or the `scripts/generate_and_upload_pdf.py` script.

3.  **Monitor Logs & Status (in respective namespaces):**
    * **MinIO Pod(s):** (`minio` namespace) `oc logs -l app=minio -n minio -f` (Check for webhook sending attempts and any errors).
    * **`minio-event-bridge` Pod(s):** (`rag-pipeline-workshop` namespace)
        `oc logs -l app=minio-event-bridge -c user-container -n rag-pipeline-workshop -f` (Look for "Received MinIO event", "Forwarded CloudEvent to broker").
        Also check `istio-proxy` logs if sidecar injected: `oc logs -l app=minio-event-bridge -c istio-proxy -n rag-pipeline-workshop -f`.
    * **Kafka Consumer (Debug Broker Topic):** (`kafka` namespace, topic `knative-broker-rag-pipeline-workshop-kafka-broker`) - to see the CloudEvents from your bridge.
```
oc exec -n kafka <your-kafka-broker-pod-name> -- bin/kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 \
    --topic knative-broker-rag-pipeline-workshop-kafka-broker --from-beginning --timeout-ms 15000
```
    * **`s3-event-handler` Knative Service Pod(s):** (`rag-pipeline-workshop` namespace)
        `oc logs -l serving.knative.dev/service=kfp-s3-trigger -c user-container -n rag-pipeline-workshop -f` (Check for "Received CloudEvent BODY" and KFP trigger logs).
        Also check `istio-proxy` sidecar logs: `oc logs -l serving.knative.dev/service=kfp-s3-trigger -c istio-proxy -n rag-pipeline-workshop -f`.
4.  **Check Kubeflow Pipelines UI:** For new pipeline runs in the "S3 Triggered PDF Runs" experiment.

---

## V. Troubleshooting

A systematic approach is key. Start from the beginning of the event flow or the failing component.

* **General:**
    * `oc get events -n <namespace> --sort-by=.metadata.creationTimestamp`: Check for recent Kubernetes events in the relevant namespace.
    * **ArgoCD UI:** Check application sync status, health, and logs for any deployment errors. If an app is out of sync, investigate the diff.
    * **Operator Health:** Ensure all required OpenShift Operators are running and healthy. Check their `ClusterServiceVersion` (CSV) status in `openshift-operators` or their designated namespaces.

* **MinIO (`minio` namespace):**
    * Pod logs for webhook sending errors (DNS for `minio-event-bridge` service, connection refused, HTTP errors from bridge).
    * `mc event list <alias>/<bucket> <webhook-arn>`: Verify `arn:minio:webhook::YOUR_ID:` rule. If `mc event add` fails, check MinIO server logs for the *actual* rejection reason.
    * **NetworkPolicy:** Ensure `services/minio/network-policies.yaml` allows egress from MinIO pods to `minio-event-bridge` pods in `rag-pipeline-workshop` on port 8080.

* **`minio-event-bridge` Service (`rag-pipeline-workshop`):**
    * `ksvc` status (`oc get ksvc minio-event-bridge -n rag-pipeline-workshop -o yaml`). Check `Ready` conditions.
    * Pod logs (`user-container`): Errors receiving from MinIO (check `/webhook` endpoint), errors creating CloudEvent, errors POSTing to `BROKER_URL`. Is `BROKER_URL` correct and reachable from this pod?
    * `istio-proxy` logs if sidecar injected and suspecting mesh issues.
    * **NetworkPolicy:** Ensure an ingress policy in `rag-pipeline-workshop` (e.g., `allow-minio-to-event-bridge` defined in `services/minio/network-policies.yaml` but ensure it targets the correct namespace if you copied it, or a dedicated one in `rag-pipeline-workshop`) allows ingress from MinIO namespace to this service on port 8080.
    * **Istio `AuthorizationPolicy`:** Ensure it allows POST from `minio` namespace (or unauthenticated if MinIO is not in mesh) to `/webhook` on `minio-event-bridge`.

* **Kafka (OpenShift Streams - `kafka` namespace):**
    * Relevant for the Knative Kafka-backed Broker. Ensure Kafka cluster and the broker's internal topic (e.g., `knative-broker-rag-pipeline-workshop-kafka-broker`) are healthy.

* **Knative Eventing (`rag-pipeline-workshop` for Trigger; `knative-eventing` or broker's namespace for Broker internals):**
    * **`Broker` Status:** (`kafka-broker`) - `oc get broker kafka-broker -n rag-pipeline-workshop -o yaml`. Needs to be `READY True`. Check its `ConfigMap` (e.g., `services/knative/config.yaml` for `kafka-broker-config`).
    * **Kafka Broker Data Plane Logs:** (Pods for `kafka-broker-receiver` / `kafka-broker-dispatcher`) `oc logs -l eventing.knative.dev/broker=kafka-broker -c <receiver/dispatcher> -n <broker-namespace> -f`.
    * **`Trigger` Status:** (`minio-pdf-event-trigger` in `services/knative/trigger.yaml`) `oc get trigger minio-pdf-event-trigger -n rag-pipeline-workshop -o yaml`. Check `status.subscriberUri` and conditions. Ensure filters (e.g., `type: "s3:ObjectCreated:Put"`, `source: "minio:s3"`) match what `minio-event-bridge` sends.

* **Knative Serving (`kfp-s3-trigger` and `minio-event-bridge` in `rag-pipeline-workshop`):**
    * **`ksvc` Status "Unknown" / "Uninitialized" / "IngressNotConfigured" / "Waiting for load balancer":**
        * **Istio Integration is Key:** Your `KnativeServing` CR uses `ingress.istio.enabled: true`.
        * **`ServiceMeshMemberRoll`:** Confirm `rag-pipeline-workshop` is a member of the `default` SMMR in `istio-system`.
        * **Sidecar Injection:** Both `minio-event-bridge` and `kfp-s3-trigger` pods *must* have `sidecar.istio.io/inject: "true"` in their `ksvc` template annotations, and the `istio-proxy` container must be present in the pods.
        * **`net-istio-controller` Logs:** (in `knative-serving` namespace) `oc logs -l app=net-istio-controller -c controller -n knative-serving -f`. Look for errors creating `VirtualService` for your ksvcs or probing errors (like 404s to `/healthz` via the gateway).
        * **`VirtualService`:** `oc get vs -n rag-pipeline-workshop`. Ensure `VirtualServices` (e.g., `kfp-s3-trigger-ingress`, `minio-event-bridge-ingress`) exist and are configured correctly, pointing to the right Istio Gateway(s) and service revisions.
        * **Istio Gateway(s) & OpenShift Route for Istio Ingress:** Verify the Istio Ingress Gateway (e.g., in `istio-system`) is running and its OpenShift Route is configured correctly. Your `ksvc` URLs must resolve to and be handled by this gateway. The Knative ingress gateway (e.g., `knative-ingress-gateway` in `knative-serving` or `istio-system`, or `kserve-local-gateway`) might need specific port configurations (e.g., port 80 for HTTP if your `ksvc` URLs are HTTP internally before edge termination, or 8443 for HTTPS with `ISTIO_MUTUAL`). The OpenShift Route exposing this Istio Gateway might need `passthrough` termination if the Istio Gateway itself handles TLS (e.g., `SIMPLE` or `ISTIO_MUTUAL` mode). If the Route uses `edge` termination, the Istio Gateway should typically listen on HTTP for that server block. *(Your debugging indicated the `knative-ingress-gateway` might be configured for HTTPS only and the OpenShift route might need to be `passthrough` if the gateway itself handles TLS with `ISTIO_MUTUAL` or `SIMPLE` mode with its own certs).*

* **Kubeflow Pipelines (OpenShift AI):**
    * **`s3-event-handler` Logs:** Check for errors when calling KFP SDK (`KFP_ENDPOINT` reachability, auth, pipeline name/ID).
    * **KFP UI:** Check for failed pipeline runs and examine their specific step logs.
    * **RBAC:** Ensure the `kfp-trigger-sa` ServiceAccount (used by `s3-event-handler`) has permissions to create KFP runs in the target KFP namespace (usually the user's project or a dedicated KFP project).

---
## VI. How to test the end to end solution for PDF upload

This section guides you through testing the complete PDF upload and processing pipeline using the provided Python script.

### 1. Set up Python Environment

Create and activate a Python virtual environment:
```
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Linux/MacOS:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install required packages
pip install python-dotenv minio faker fpdf2
```

### 2. Configure Environment Variables

1. Copy the example environment file:
```
cp scripts/example.env .env
```

2. Edit `.env` and set the following variables:
```
# MinIO connection details
MINIO_ENDPOINT=<your-minio-route-without-http/https>  # e.g., minio-minio.apps.your-cluster.com
MINIO_ACCESS_KEY=<your-minio-access-key>
MINIO_SECRET_KEY=<your-minio-secret-key>
MINIO_SECURE=true  # Use HTTPS if your MinIO route is HTTPS, false for HTTP
MINIO_BUCKET_NAME=pdf-inbox  # Or your chosen bucket name

# Optional: PDF generation settings
PDF_COUNT=1  # Number of PDFs to generate
PDF_PAGES_MIN=1  # Minimum pages per PDF
PDF_PAGES_MAX=5  # Maximum pages per PDF
```

### 3. Run the Upload Script

The script `scripts/generate_and_upload_pdf.py` will generate sample PDF files with random content and upload them to MinIO:
```
`# Ensure virtual environment is activated and .env is populated
python scripts/generate_and_upload_pdf.py
```

OR you can manually trigger the pipeline as followed
```
oc exec -n minio debug-pod -- curl -v -X POST http://minio-event-bridge.rag-pipeline-workshop.svc.cluster.local:8080/webhook -H "Content-Type: application/json" -d '{
  "EventName": "s3:ObjectCreated:Put",
  "Key": "pdf-inbox/test.pdf",
  "Records": [{
    "eventVersion": "2.0",
    "eventSource": "minio:s3",
    "awsRegion": "",
    "eventTime": "2023-12-14T10:00:00.000Z",
    "eventName": "s3:ObjectCreated:Put",
    "userIdentity": {
      "principalId": "minioadmin"
    },
    "requestParameters": {
      "sourceIPAddress": "127.0.0.1"
    },
    "responseElements": {
      "x-amz-request-id": "test123",
      "x-minio-deployment-id": "test123"
    },
    "s3": {
      "s3SchemaVersion": "1.0",
      "configurationId": "Config",
      "bucket": {
        "name": "pdf-inbox",
        "ownerIdentity": {
          "principalId": "minioadmin"
        },
        "arn": "arn:aws:s3:::pdf-inbox"
      },
      "object": {
        "key": "test.pdf",
        "size": 1024,
        "eTag": "test123",
        "contentType": "application/pdf",
        "sequencer": "test123"
      }
    },
    "source": {
      "host": "minio",
      "port": "",
      "userAgent": "MinIO"
    }
  }]
}'
Note: Unnecessary use of -X or --request, POST is already inferred.
* Host minio-event-bridge.rag-pipeline-workshop.svc.cluster.local:8080 was resolved.
* IPv6: (none)
* IPv4: 172.30.186.14
*   Trying 172.30.186.14:8080...
* Connected to minio-event-bridge.rag-pipeline-workshop.svc.cluster.local (172.30.186.14) port 8080
* using HTTP/1.x
> POST /webhook HTTP/1.1
> Host: minio-event-bridge.rag-pipeline-workshop.svc.cluster.local:8080
> User-Agent: curl/8.13.0
> Accept: */*
> Content-Type: application/json
> Content-Length: 726
> 
* upload completely sent off: 726 bytes
< HTTP/1.1 200 OK
< server: envoy
< date: Sat, 31 May 2025 22:25:50 GMT
< content-type: application/json
< content-length: 89
< x-envoy-upstream-service-time: 10054
< 
{"broker_response":503,"message":"Event forwarded to Knative broker","status":"success"}
* Connection #0 to host minio-event-bridge.rag-pipeline-workshop.svc.cluster.local left intact
```
### 4. Monitor the Pipeline

After uploading PDFs, you can monitor the event flow:

1. Check MinIO webhook delivery:
```
# View MinIO pod logs
oc logs -l app=minio -n minio -f
```

2. Verify `minio-event-bridge` reception and CloudEvent creation:
```
# View minio-event-bridge logs
oc logs -l app=minio-event-bridge -c user-container -n rag-pipeline-workshop -f
```

3. Monitor `s3-event-handler` (PDF processing trigger):
```{# BEGIN_CODE_BLOCK bash #}```
# View s3-event-handler logs
oc logs -l serving.knative.dev/service=kfp-s3-trigger -c user-container -n rag-pipeline-workshop -f
```

oc delete mutatingwebhookconfigurations webhook.serving.knative.dev && oc delete validatingwebhookconfigurations config.webhook.serving.knative.dev validation.webhook.serving.knative.dev

4. Check Kubeflow Pipeline execution in the OpenShift AI dashboard under the "S3 Triggered PDF Runs" experiment.

### Troubleshooting Upload Issues

- **Connection errors**: Verify MinIO endpoint and credentials in `.env`. Check `MINIO_SECURE` matches the endpoint protocol.
- **SSL/TLS errors**: If `MINIO_SECURE=true`, ensure your local system trusts the certificate for the MinIO endpoint, or that the MinIO route is correctly configured for TLS.
- **Permission denied**: Check MinIO bucket permissions and access policy for the user/keys used by the script.
- **Generate script errors**: Ensure all required Python packages (`python-dotenv`, `minio`, `faker`, `fpdf2`) are installed in your virtual environment.

---
## VII. Contributing

1.  Fork the repository.
2.  Create a feature branch (`git checkout -b feature/my-new-feature`).
3.  Commit your changes (`git commit -am 'Add some feature'`).
4.  Push to the branch (`git push origin feature/my-new-feature`).
5.  Create a new Pull Request.

---

## VIII. License

This project is licensed under the MIT License - see the `LICENSE` file for details.