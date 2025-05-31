# Mock Ticketing & Event-Driven Document Processing System on OpenShift
This project deploys a multifaceted system on OpenShift, featuring:

A ServiceNow-like Mock API for simulating incident and service request management.

An Event-Driven Serverless PDF Processing Pipeline: When a PDF is uploaded to a MinIO S3 bucket, an event is triggered via Apache Kafka and Knative Eventing, ultimately launching a Kubeflow Pipeline for processing.

Supporting infrastructure including MinIO, Apache Kafka, and Knative components, with Istio (via OpenShift Service Mesh) as the Knative ingress.

The entire system is designed for containerized deployment and is managed via GitOps principles using ArgoCD and Kustomize. It leverages key OpenShift Operators for robust functionality, demonstrating a cloud-native, event-driven architecture suitable for scalable data ingestion and processing workflows.

Overall Architecture
The system is architecturally divided into an event-driven data pipeline and a standalone mock API, orchestrated and managed using GitOps.

## 1. Event-Driven PDF Processing Workflow
This workflow is initiated by a PDF upload and culminates in a Kubeflow Pipeline execution:

MinIO (S3 Storage): Serves as the primary object storage for incoming PDF files. It's deployed on OpenShift and should ideally leverage OpenShift Data Foundation (ODF/OCS) for persistent and resilient storage in production.

MinIO Bucket Notification to Kafka: Upon a PDF upload to a designated bucket (e.g., pdf-inbox), MinIO is configured (via server-side environment variables in its Deployment YAML and linked to a specific bucket using the mc event add command) to publish a notification message directly to an Apache Kafka topic.

Apache Kafka (via OpenShift Streams Operator): Acts as a durable, scalable, and fault-tolerant event bus. It receives raw event notifications from MinIO into a specific topic (e.g., minio-bucket-notifications).

Knative KafkaSource: This Knative Eventing component subscribes to the MinIO events topic in Kafka. It consumes the raw JSON messages from MinIO and transforms them into structured CloudEvents.

Knative Eventing Broker (Kafka-backed): The KafkaSource forwards these CloudEvents to a Kafka-backed Knative Broker (e.g., kafka-broker). This broker provides content-based routing and decouples event producers (KafkaSource) from event consumers. The broker itself uses Kafka topics for its data plane, managed by the OpenShift Streams Operator.

Knative Trigger: Subscribes to specific CloudEvents from the kafka-broker (e.g., filtering for PDF creation events based on attributes set by the KafkaSource).

s3-event-handler (Knative Service): A Python Flask application deployed as a serverless function using Knative Serving. It's the subscriber for the Knative Trigger. Upon receiving a CloudEvent, it parses the data payload (which contains the original MinIO event details) to extract PDF metadata (bucket, key). This service runs within the Istio service mesh.

Kubeflow Pipelines (via OpenShift AI Operator): The s3-event-handler uses the Kubeflow Pipelines (KFP) SDK to trigger a specific pipeline (e.g., pdf-to-docling) in Kubeflow, passing the PDF information for further processing. The KFP orchestrates the actual PDF processing tasks, which might involve GPU-accelerated steps.

Istio (via OpenShift Service Mesh Operator): Functions as the ingress controller for Knative Serving. It manages external and internal traffic to Knative Services like s3-event-handler through Istio Gateways and VirtualServices. The namespace hosting the Knative Service (rag-pipeline-workshop) must be part of the Service Mesh.

## 2. ServiceNow Mock API
An independent Flask/Gunicorn REST API providing mock ticketing data for incidents and service requests.

Deployed as a standard OpenShift application (Deployment, Service, Route).

An optional Tekton CI/CD pipeline is provided for building and deploying this API.

(For a visual representation, please refer to the PlantUML diagram in the plantuml_architecture_diagram_v2 artifact, which can be rendered in tools like draw.io or directly via PlantUML.)

## Required OpenShift Operators
Successful deployment and operation of this project depend on the following OpenShift Operators being installed and configured:

OpenShift AI Operator: Essential for deploying and managing Kubeflow Pipelines, which orchestrates the PDF processing tasks. Also provides environments like JupyterHub for development.

Nvidia GPU Operator: (Conditional) If your Kubeflow Pipelines include steps requiring GPU acceleration (e.g., for ML model inference on PDFs), this operator is necessary to manage and expose NVIDIA GPU resources to pods.

Node Feature Operator (NFO): Often a prerequisite or companion to the GPU Operator. NFO discovers and labels hardware features on cluster nodes, enabling GPU-aware scheduling.

OpenShift Serverless Operator: Core to the event-driven architecture. It installs and manages:

Knative Serving: For deploying and running serverless applications like the s3-event-handler.

Knative Eventing: For managing the event flow, including Brokers, Triggers, and Sources like KafkaSource.

OpenShift Service Mesh Operator: Installs and manages Istio. In this setup, Istio is configured as the ingress controller for Knative Serving, handling traffic routing to serverless applications. It also enables mesh capabilities like mTLS and traffic policies if configured. The application namespace (rag-pipeline-workshop) must be added to the ServiceMeshMemberRoll.

OpenShift GitOps Operator: Installs and manages ArgoCD. This is fundamental for the GitOps workflow, automating the deployment and lifecycle management of all applications and services defined in this Git repository.

OpenShift Streams Operator (AMQ Streams): Deploys and manages Apache Kafka clusters on OpenShift. Kafka is used here as the robust message bus for MinIO event notifications.

OpenShift Data Foundation (ODF/OCS): (Recommended for Production) Provides enterprise-grade persistent, replicated storage. Crucial for stateful services like MinIO (for object storage), Apache Kafka (for message logs), and Kubeflow Pipelines (for artifacts and metadata). For development/PoC, ephemeral storage or standard PVCs with a basic StorageClass can be used, but ODF is recommended for resilience and scalability.

## GitOps with ArgoCD
This entire project is designed to be managed using a GitOps workflow powered by ArgoCD (installed via the OpenShift GitOps Operator).

Kustomize: Each component's Kubernetes/OpenShift manifests are organized into directories with kustomization.yaml files (e.g., in apps/api/.k8s/, services/minio/, services/kafka/, services/knative/, apps/s3-event-handler/.openshift/). This allows for base configurations and environment-specific overlays (though not explicitly detailed here, it's a common Kustomize pattern).

App of Apps Pattern: A root ArgoCD Application (defined in bootstrap/app-of-apps.yaml) manages a set of child ArgoCD Applications. Each child application (defined in bootstrap/) points to a Kustomize configuration for a specific component (e.g., MinIO, Kafka, Knative resources, Mock API, S3 Event Handler).

Declarative State: The Git repository is the single source of truth. All desired states of the applications and infrastructure components are declared in YAML. ArgoCD continuously monitors the Git repository and applies changes to the cluster to match this desired state. This ensures consistency, auditability, and automated rollbacks/rollouts.
```
Project Structure
.
├── apps
│   ├── api                     # ServiceNow Mock API (Flask app, Dockerfile, requirements)
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │   └── .k8s/               # Kubernetes manifests for the API (managed by ArgoCD app-mock-api.yaml)
│   │       ├── deployment.yaml
│   │       ├── kustomization.yaml
│   │       ├── service.yaml
│   │       └── route.yaml
│   └── s3-event-handler        # Serverless KFP Trigger (Knative Service - Flask app, Dockerfile, requirements)
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       └── .openshift/         # Knative Service, Trigger, RBAC YAMLs (managed by ArgoCD app-event-handler.yaml)
│           ├── knative-service.yaml # This needs sidecar.istio.io/inject: "true" & correct ingress class
│           ├── kustomization.yaml
│           ├── trigger.yaml    # Subscribes to kafka-broker, filters KafkaSource events
│           └── rbac.yaml
├── bootstrap/                  # ArgoCD Application definitions
│   ├── app-event-handler.yaml  # ArgoCD app for s3-event-handler & its Knative Trigger/RBAC
│   ├── app-kafka.yaml          # ArgoCD app for Kafka cluster & topic (points to services/kafka/)
│   ├── app-knative-broker.yaml # ArgoCD app for Knative Kafka-backed Broker & KafkaSource (points to services/knative/)
│   ├── app-minio.yaml          # ArgoCD app for MinIO deployment (points to services/minio/)
│   ├── app-mock-api.yaml       # ArgoCD app for ServiceNow Mock API (points to apps/api/.k8s/)
│   ├── app-of-apps.yaml        # Root ArgoCD application managing all other apps
│   └── kustomization.yaml      # Kustomization for all bootstrap ArgoCD applications
├── LICENSE
├── pipeline
│   └── pdf-to-docling          # Kubeflow Pipeline definition
│       └── pdf-pipeline.py
├── README.md
├── scripts/
│   ├── example.env             # Example environment variables
│   └── setup_minio_events.sh   # Script to configure MinIO bucket notifications to Kafka
└── services                    # Kubernetes/OpenShift manifests for backing services
    ├── kafka/                  # Kafka cluster (Strimzi) and topic definitions
    │   ├── kafka-cluster.yaml  # Defines 'my-kafka-cluster'
    │   ├── kafka-topic-minio-notifications.yaml # Defines 'minio-bucket-notifications' topic
    │   └── kustomization.yaml
    ├── knative/                # Knative Eventing components (Broker, Source, ConfigMap)
    │   ├── base/               # Base configurations for Knative (e.g. default broker class for knative-eventing ns)
    │   │   ├── config-br-defaults.yaml # Example: Sets default broker class to Kafka globally
    │   │   └── kustomization.yaml
    │   ├── broker.yaml         # Defines 'kafka-broker' (Kafka-backed) in app namespace
    │   ├── kafka-broker-config.yaml # ConfigMap for the 'kafka-broker'
    │   ├── kafkasource-minio.yaml # Defines Knative KafkaSource for MinIO events
    │   └── kustomization.yaml  # Bundles broker.yaml, kafka-broker-config.yaml, kafkasource-minio.yaml
    └── minio/                  # MinIO S3 Storage deployment
        ├── create-secret.yaml
        ├── deployment.yaml     # This MUST contain the MINIO_NOTIFY_KAFKA_* env vars
        ├── kustomization.yaml
        ├── namespace.yaml
        ├── pvc.yaml
        ├── route.yaml
        ├── sa.yaml
        └── service.yaml
├── .tekton/                    # (Optional) Tekton CI/CD resources for Mock API
│   ├── pipeline.yaml
│   ├── pipeline_run.yaml
│   ├── buildah-task.yaml       # Custom Buildah task definition (if not using catalog)
│   ├── kustomization.yaml
│   └── tekton-pvc.yaml
```
(Note: The trigger.yaml for the s3-event-handler is located in apps/s3-event-handler/.openshift/ and managed by app-event-handler.yaml via ArgoCD. The services/knative/kustomization.yaml bundles the Kafka-backed Broker, its ConfigMap, and the KafkaSource.)

ServiceNow Mock API
A Flask-based REST API that provides mock ServiceNow functionality, focusing on closed ticket data.

## Technical Stack (Mock API)
Python 3.11

Flask, Gunicorn

Red Hat Universal Base Image (UBI) 9

Features (Mock API)
Incident Management: CRUD operations, filtering, pagination.

Service Request Management: CRUD operations, filtering, pagination.

Health Check Endpoint: /api/v1/health.

## II. Serverless S3 -> Kubeflow Pipeline Trigger
An event-driven serverless application that listens for PDF file uploads to MinIO (via Kafka and Knative Eventing with Istio ingress) and triggers a Kubeflow Pipeline.

Technical Stack (Serverless Trigger)
Python 3.11, Flask (for Knative service)

Kubeflow Pipelines SDK (kfp)

Red Hat Universal Base Image (UBI) 9

MinIO for S3 storage

Apache Kafka (via OpenShift Streams) for event brokering from MinIO

OpenShift Serverless (Knative Serving and Eventing - KafkaSource, Broker, Trigger)

Istio (via OpenShift Service Mesh) as the ingress for Knative Serving.

Kubeflow Pipeline (Stub)
Located at pipeline/pdf-to-docling/pdf-pipeline.py.

A simple pipeline that accepts S3 bucket/key for a PDF and logs this information.

This pipeline needs to be compiled (e.g., python pipeline/pdf-to-docling/pdf-pipeline.py produces simple_pdf_pipeline.tar.gz) and uploaded to your Kubeflow Pipelines instance on OpenShift AI.

## Setup and Deployment (GitOps with ArgoCD)
This project is designed to be deployed and managed using ArgoCD.

### Prerequisites
OpenShift Cluster (4.x).

Required Operators Installed (see "Required OpenShift Operators" section above). Ensure they are healthy and configured.

Git repository hosting this project structure.

OpenShift CLI (oc) installed locally.

MinIO Client (mc) installed locally for initial bucket/event setup.

(Optional) Podman/Docker for building images if not using pre-built ones.

(Optional) Tekton CLI (tkn).

### Initial Setup Steps:
Clone the Repository & Customize Placeholders:

Clone this Git repository.

Review all YAML files (especially in bootstrap/, services/, and application-specific manifest directories like apps/api/.k8s/ and apps/s3-event-handler/.openshift/) for placeholder values. Update YOUR_GIT_REPO_URL, namespaces (e.g., rag-pipeline-workshop, your-api-project, kafka, minio, your-tekton-pipelines-namespace, istio-system, knative-eventing), image URLs, and secrets to match your environment.

Pay special attention to services/minio/create-secret.yaml for MinIO credentials and services/minio/deployment.yaml for MINIO_NOTIFY_KAFKA_* environment variables (ensure the Kafka broker address like my-kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092 and topic minio-bucket-notifications are correct, and the ID like MYMINIOKAFKA is consistent).

Update scripts/example.env with your specific values. You can source scripts/example.env to set them in your shell before running scripts.

Configure Namespace for Istio Service Mesh (Critical for s3-event-handler):
Your Knative Services (like kfp-s3-trigger) will use Istio for ingress, as defined in your KnativeServing CR (spec.ingress.istio.enabled: true). The namespace where these services run (e.g., rag-pipeline-workshop) must be part of the Istio Service Mesh for sidecar injection and proper routing.

Identify your Istio control plane namespace (usually istio-system).

Add Application Namespace to ServiceMeshMemberRoll:

heck current members (replace 'istio-system' if your SMCP is elsewhere)
```
oc get smmr default -n istio-system -o yaml 
```
If rag-pipeline-workshop (or your serverless namespace) is not in spec.members:

Edit the ServiceMeshMemberRoll to add your namespace
```
oc edit smmr default -n istio-system 
```
Add rag-pipeline-workshop to the spec.members list:


```
spec:
  members:
    - knative-serving                         # Keep existing members
    - redhat-ods-applications-auth-provider # Keep existing members
    - rag-pipeline-workshop                 # <-- ADD YOUR NAMESPACE
```


Note: This step might ideally be managed via GitOps by having a manifest for the SMMR if you have cluster-admin rights or a process to update it. If not, it's a manual prerequisite.

Ensure Knative Service YAML requests sidecar injection:
Verify that apps/s3-event-handler/.openshift/knative-service.yaml includes the following in spec.template.metadata.annotations for the kfp-s3-trigger service:
```
networking.knative.dev/ingress.class: "istio.ingress.networking.knative.dev" # Essential
sidecar.istio.io/inject: "true"                                         # Essential for mesh participation
# sidecar.istio.io/rewriteAppHTTPProbers: "true" # Often useful for probes with Istio
```
Allow a minute or two for the Service Mesh Operator to reconcile the namespace. New pods for Knative Services in rag-pipeline-workshop should now get the Istio sidecar.

Push Changes to Your Git Repository: Commit all customized files.

Apply the Root ArgoCD Application:

Apply to the namespace where ArgoCD is running (typically 'openshift-gitops')
```
oc apply -f bootstrap/app-of-apps.yaml -n openshift-gitops
```
ArgoCD will deploy all defined child applications and their resources. Monitor sync status in the ArgoCD UI.

Post-Deployment MinIO Configuration (Bucket and Event Notifications):
Once MinIO is deployed by ArgoCD and its Deployment includes the Kafka environment variables:

Run the setup_minio_events.sh script: This configures your MinIO bucket to send PDF upload events to the server-configured Kafka target.

Ensure environment variables from scripts/example.env are set and sourced.
KAFKA_TARGET_ID_IN_MINIO in the script MUST match the ID used in MinIO's env vars (e.g., "MYMINIOKAFKA").
```
cd scripts/
chmod +x setup_minio_events.sh
./setup_minio_events.sh
cd ..
```
This script will:

Set up an mc alias.

Create the target S3 bucket (e.g., pdf-inbox) if it doesn't exist.

Add an event notification rule to the bucket, using an ARN like arn:minio:kafka::YOUR_KAFKA_TARGET_ID:, which points to the Kafka target defined by the MinIO server's environment variables.

Prepare and Upload Kubeflow Pipeline:

Navigate to pipeline/pdf-to-docling/.

Compile the pipeline: python pdf-pipeline.py (generates simple_pdf_pipeline.tar.gz).

Upload this simple_pdf_pipeline.tar.gz to your Kubeflow Pipelines UI. Note the Pipeline Name (e.g., "Simple PDF Processing Pipeline"). Ensure the KFP_PIPELINE_NAME in apps/s3-event-handler/.openshift/knative-service.yaml for kfp-s3-trigger matches this.

Tekton CI/CD Pipeline (Optional, for Mock API)
The .tekton/ directory contains resources for an optional Tekton pipeline to build and deploy the ServiceNow Mock API. These resources are managed by the bootstrap/app-mock-api-tekton.yaml ArgoCD application.

The pipeline uses when clauses based on parameters in the PipelineRun to make phases (fetch, build, deploy) skippable.

Prerequisites: Ensure Tekton ClusterTasks (git-clone, buildah), and your custom buildah-task.yaml (if not using a catalog version) are available. The .tekton/tekton-pvc.yaml will be created by ArgoCD. Create a registry secret (e.g., quay-credentials) in your Tekton namespace (e.g., your-tekton-pipelines-namespace).

Usage: To trigger a run manually (if not automated), modify and apply a PipelineRun YAML (e.g., based on .tekton/pipeline_run.yaml), setting parameters like Git URL, image URL, and skip flags.

### API Usage & Testing
Mock API Endpoints
Health Check: GET /api/v1/health

Response: {"status": "UP"}

Incidents:

List: GET /api/v1/incidents (Query params: limit, offset, state, priority, etc.)

Get single: GET /api/v1/incidents/<incident_number>

Create: POST /api/v1/incidents (Body: {"short_description": "...", "caller_id": "...", "description": "..."})

Update: PUT/PATCH /api/v1/incidents/<incident_number>

Service Requests: Similar CRUD endpoints under /api/v1/requests.

Example curl (replace <mock-api-route> with actual route URL from oc get route -n your-api-project):

`curl http://<mock-api-route>/api/v1/incidents?limit=2 | jq`

Testing Event-Driven Flow
Verify kfp-s3-trigger Knative Service is READY:
```
oc get ksvc kfp-s3-trigger -n rag-pipeline-workshop
# Expected output should show READY True True True
```
Test its health endpoint (get URL from oc get ksvc ...):

```
curl -k https://<kfp-s3-trigger-route-url>/healthz
# Expected: {"message":"Application is running","status":"healthy"}
```

Upload a PDF file to the MinIO bucket configured for notifications (e.g., pdf-inbox on your osminio alias).

Monitor Logs & Status (in respective namespaces):

MinIO Pod(s): (minio namespace) oc logs -l app=minio -n minio -f (Check for Kafka publishing messages or errors like DNS lookup failures for Kafka).

Kafka Consumer (Debug): (kafka namespace, minio-bucket-notifications topic)

```
oc exec -n kafka kafka-cluster-kafka-0 -- bin/kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 \
    --topic minio-bucket-notifications --from-beginning --timeout-ms 15000
```
Knative KafkaSource Pod(s): (rag-pipeline-workshop namespace)
```
oc logs -l eventing.knative.dev/source=<your-kafkasource-name> -n rag-pipeline-workshop -c dispatcher -f (e.g., minio-kafka-event-source). Look for messages about consuming from Kafka and sending to sink (Broker).
```
Knative Kafka Broker Receiver Pod(s): (e.g., in knative-eventing or rag-pipeline-workshop if broker is namespaced there)
```
oc logs -l eventing.knative.dev/broker=kafka-broker -c receiver -n <broker-namespace> -f.
```
s3-event-handler Knative Service Pod(s): (rag-pipeline-workshop namespace)
```
oc logs -l serving.knative.dev/service=kfp-s3-trigger -c user-container -n rag-pipeline-workshop -f (Check for "Received CloudEvent BODY" and KFP trigger logs).
Also check istio-proxy sidecar logs if present:
oc logs -l serving.knative.dev/service=kfp-s3-trigger -c istio-proxy -n rag-pipeline-workshop -f
```
Check Kubeflow Pipelines UI: For new pipeline runs in the "S3 Triggered PDF Runs" experiment.

V. Troubleshooting
A systematic approach is key. Start from the beginning of the event flow or the failing component.

General:
```
oc get events -n <namespace> --sort-by=.metadata.creationTimestamp: Check for recent Kubernetes events.
```
ArgoCD UI: Check application sync status, health, and logs for any deployment errors. If an app is out of sync, identify why (e.g., manual changes in cluster not reflected in Git, or sync errors).

Operator Health: Ensure all required OpenShift Operators (Serverless, Streams, AI, GitOps, Service Mesh, ODF) are running and healthy in their respective namespaces (e.g., openshift-operators, openshift-serverless, openshift-gitops, openshift-ai, istio-system, openshift-storage). Check their ClusterServiceVersion (CSV) status.

MinIO (minio namespace):

Pod Status: oc get pods -n minio (Ensure running, no restarts).

Logs: `oc logs -l app=minio -n minio -f`. Look for startup errors, Kafka connection errors (DNS, auth), or event publishing errors.

Event Notification Config: mc event list <your-alias>/<your-bucket> <target-ARN> (e.g., mc event list osminio/pdf-inbox arn:minio:kafka::MYMINIOKAFKA:). Ensure it's active and correct. If mc event add fails, check MinIO server logs for the reason the server rejected the request (it might be more specific than mc's output).

Connectivity to Kafka: If MinIO logs show Kafka connection issues ("no such host", "connection refused"), verify MINIO_NOTIFY_KAFKA_BROKERS_YOURID in MinIO deployment points to the correct internal Kafka bootstrap service FQDN (e.g., my-kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092). Test DNS resolution from within the MinIO pod:

```
MINIO_POD=$(oc get pod -n minio -l app=minio -o jsonpath='{.items[0].metadata.name}')
oc exec -n minio $MINIO_POD -- nslookup my-kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local
```
Kafka (OpenShift Streams - kafka namespace):

Cluster Health: `oc get kafka my-kafka-cluster -n kafka -o yaml` (Check status and conditions).

Pod Status: `oc get pods -n kafka` (Zookeeper and Kafka broker pods should be running and ready).

Topic Existence: `oc exec -n kafka <kafka-broker-pod> -- bin/kafka-topics.sh --bootstrap-server localhost:9092 --list | grep minio-bucket-notifications`.

Topic Describe: `oc exec -n kafka <kafka-broker-pod> -- bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic minio-bucket-notifications`. Check partitions and replicas.

Strimzi Operator Logs: (Usually in openshift-operators or its own namespace) for issues managing Kafka CRs.

Knative Serving (kfp-s3-trigger in rag-pipeline-workshop):

ksvc Status: `oc get ksvc kfp-s3-trigger -n rag-pipeline-workshop -o yaml`. Pay close attention to status.conditions (ConfigurationsReady, RoutesReady, Ready). Messages like "Waiting for load balancer," "IngressNotConfigured," or "RevisionFailed" are key.

Pod Status: `oc get pods -n rag-pipeline-workshop -l serving.knative.dev/service=kfp-s3-trigger`. Ensure pods are Running and READY (e.g., 3/3 if Istio sidecar is injected).

Pod Logs:

user-container: `oc logs <pod> -c user-container -n rag-pipeline-workshop -f`. Check for Flask/Gunicorn startup, /healthz responses (404s mean the endpoint isn't defined or app isn't running correctly), and event processing logic errors.

queue-proxy: `oc logs <pod> -c queue-proxy -n rag-pipeline-workshop -f`. Check for probe failures (e.g., 404 to /healthz if user-container isn't responding).

istio-proxy (if injected): `oc logs <pod> -c istio-proxy -n rag-pipeline-workshop -f`. Check for errors connecting to Istiod or routing issues.

Istio Integration (if ksvc not Ready or "Waiting for load balancer"):

ServiceMeshMemberRoll: Confirm rag-pipeline-workshop is in oc get smmr default -n istio-system -o yaml under spec.members and status.configuredMembers.

Sidecar Injection: Confirm istio-proxy is present in the kfp-s3-trigger pod (`oc get pod <pod-name> -o jsonpath='{.spec.containers[*].name}'`). If not, check namespace label istio-injection=enabled and pod annotation sidecar.istio.io/inject: "true".

net-istio-controller Logs: (in knative-serving namespace) `oc logs -l app=net-istio-controller -c controller -n knative-serving -f`. Look for errors creating/updating VirtualService for kfp-s3-trigger or probing errors.

VirtualService: `oc get vs -n rag-pipeline-workshop`. Ensure kfp-s3-trigger-ingress and/or kfp-s3-trigger-mesh exist. Examine their YAML (oc get vs <name> -n rag-pipeline-workshop -o yaml) for correct hosts, gateways, and route destinations.

Istio Gateway(s): (istio-system or knative-serving) oc get gateway -A. Check the ones referenced by the VirtualService and the config-istio ConfigMap in knative-serving. Ensure they are correctly configured and their selector matches the Istio ingress gateway pods.

OpenShift Route for Istio Gateway: `oc get routes -n istio-syste`m`. Ensure the main Istio ingress route is healthy and its host matches what Knative expects for external URLs.

Knative Ingress CR: `oc get ingresses.networking.internal.knative.dev kfp-s3-trigger -n rag-pipeline-workshop -o yaml`. Check its status and conditions.

Knative Eventing (rag-pipeline-workshop for Source/Trigger, knative-eventing or broker's namespace for Broker internals):

KafkaSource Status: (minio-kafka-event-source in rag-pipeline-workshop) `oc get kafkasource minio-kafka-event-source -n rag-pipeline-workshop -o yaml`. Check status.conditions for readiness and sink URI.

KafkaSource Dispatcher Logs: `oc logs -l eventing.knative.dev/source=minio-kafka-event-source -c dispatcher -n rag-pipeline-workshop -f`. Look for Kafka connection errors, message consumption, and forwarding to sink (Broker). Check consumer group lag using Kafka tools.

Broker Status: (kafka-broker in rag-pipeline-workshop) `oc get broker kafka-broker -n rag-pipeline-workshop -o yaml`. Check status.conditions and status.address.url. Ensure it's using the Kafka class and referencing the correct kafka-broker-config ConfigMap.

Kafka Broker Data Plane Logs: (Pods for kafka-broker-receiver / kafka-broker-dispatcher, often in knative-eventing or rag-pipeline-workshop if broker is namespaced there) oc logs -l eventing.knative.dev/broker=kafka-broker -c <receiver/dispatcher> -n <namespace> -f.

Trigger Status: (minio-pdf-event-trigger in rag-pipeline-workshop) `oc get trigger minio-pdf-event-trigger -n rag-pipeline-workshop -o yaml`. Check status.subscriberUri and conditions. Ensure filters (type: "dev.knative.kafka.event", source: "/kafkasource/...") are correct for events from KafkaSource. If DependencyNotReady or SubscriberResolved: False, it points to issues with the kfp-s3-trigger ksvc.

The Knative service route is using edge termination, but we're using ISTIO_MUTUAL TLS in the gateway. Let's:
1. First check the istio-ingressgateway service configuration
2. Then patch the route to use passthrough termination to match our gateway's TLS settings
oc patch route route-4cba4433-a979-46f3-b67e-5099b5857894-616331383865 -n istio-system -p '{"spec":{"tls":{"termination":"passthrough","insecureEdgeTerminationPolicy":"None"}}}' --type=merge
route.route.openshift.io/route-4cba4433-a979-46f3-b67e-5099b5857894-616331383865 patched

Kubeflow Pipelines (OpenShift AI):

s3-event-handler Logs: Check for errors when calling KFP SDK (KFP_ENDPOINT reachability, authentication to KFP, pipeline name/ID validity, experiment name).

KFP UI: Check for failed pipeline runs and examine their specific step logs.

RBAC: Ensure the kfp-trigger-sa ServiceAccount (used by s3-event-handler) has permissions to create KFP runs in the target KFP namespace/project.