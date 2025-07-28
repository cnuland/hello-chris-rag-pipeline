# Mock Ticketing & Event-Driven Document Processing System on OpenShift

This project deploys a multifaceted system on OpenShift, featuring:
1.  A **ServiceNow-like Mock API** for simulating incident and service request management.
2.  An **Event-Driven Serverless PDF Processing Pipeline**: When a PDF is uploaded to a MinIO S3 bucket, an event is triggered via Apache Kafka and Knative Eventing, ultimately launching a Kubeflow Pipeline for processing.
3.  A **Milvus Vector Database** for storing embeddings and enabling Retrieval-Augmented Generation (RAG) capabilities.
4.  Supporting infrastructure including **MinIO**, **Apache Kafka**, and **Knative**, with OpenShift Serverless's default ingress (Kourier).

The entire system is designed for containerized deployment and is managed via **GitOps principles using ArgoCD and Kustomize**. It leverages key OpenShift Operators for robust functionality, demonstrating a cloud-native, event-driven architecture suitable for scalable data ingestion and processing workflows.

## Overall Architecture

The system is architecturally divided into an event-driven data pipeline, a vector database, and a standalone mock API, all orchestrated and managed using GitOps.

### 1. Event-Driven PDF Processing Workflow

This workflow is initiated by a PDF upload and culminates in a Kubeflow Pipeline execution:

* **MinIO (S3 Storage):** Serves as the primary object storage for incoming PDF files. It's deployed on OpenShift and should ideally leverage OpenShift Data Foundation (ODF/OCS) for persistent and resilient storage in production.
* **MinIO Bucket Notification to Kafka:** Upon a PDF upload to a designated bucket (e.g., `pdf-inbox`), MinIO is configured to publish a notification message directly to an Apache Kafka topic.
* **Apache Kafka (via OpenShift Streams Operator):** Acts as a durable, scalable, and fault-tolerant event bus. It receives raw event notifications from MinIO into a specific topic (e.g., `minio-bucket-notifications`).
* **Knative KafkaSource:** This Knative Eventing component subscribes to the MinIO events topic in Kafka. It consumes the raw JSON messages and transforms them into structured CloudEvents.
* **Knative Eventing Broker (Kafka-backed):** The KafkaSource forwards these CloudEvents to a Kafka-backed Knative Broker (e.g., `kafka-broker`). This broker provides content-based routing and decouples event producers from consumers.
* **Knative Trigger:** Subscribes to specific CloudEvents from the `kafka-broker` (e.g., filtering for PDF creation events).
* **`s3-event-handler` (Knative Service):** A Python Flask application deployed as a serverless function using Knative Serving. It's the subscriber for the Knative Trigger. Upon receiving a CloudEvent, it parses the payload to extract PDF metadata and uses the KFP SDK to trigger a processing pipeline.
* **Kubeflow Pipelines (via OpenShift AI Operator):** Orchestrates the data processing workflows.
    * **Docling Pipeline:** A pipeline designed to take a PDF from S3, process it with the `docling` library to extract text and structure, and prepare it for embedding.
    * **Milvus Ingestion Pipeline:** A pipeline that takes structured data (from an API or from the Docling pipeline's output), generates vector embeddings, and ingests them into the Milvus database.
* **OpenShift Serverless Ingress (Kourier):** The default ingress controller for Knative Serving handles external and internal traffic to Knative Services like `s3-event-handler`.

### 2. Milvus Vector Database

* A standalone Milvus instance is deployed into each user's namespace.
* It provides the vector storage and search capabilities required for the RAG functionality.
* It is accessed internally by the KFP pipelines.

### 3. ServiceNow Mock API

* An independent Flask/Gunicorn REST API providing mock ticketing data for incidents and service requests.
* Deployed as a standard OpenShift application (Deployment, Service, Route).

*(For a visual representation, please refer to the PlantUML diagram in the `plantuml_architecture_diagram_v2` artifact, which can be rendered in tools like draw.io or directly via PlantUML.)*

---

## Required OpenShift Operators

Successful deployment of this project depends on the following OpenShift Operators being installed and configured:

* **OpenShift AI Operator:** Essential for deploying and managing Kubeflow Pipelines, which orchestrates the data processing tasks.
* **Nvidia GPU Operator:** (Conditional) Required if any Kubeflow Pipeline components or other workloads need GPU acceleration.
* **OpenShift Serverless Operator:** Core to the event-driven architecture. It installs and manages:
    * **Knative Serving:** For deploying and running serverless applications like the `s3-event-handler`.
    * **Knative Eventing:** For managing the event flow, including Brokers, Triggers, and Sources like `KafkaSource`.
* **OpenShift GitOps Operator:** Installs and manages ArgoCD, enabling the GitOps workflow for deploying all applications and services.
* **OpenShift Streams Operator (AMQ Streams):** Deploys and manages Apache Kafka clusters on OpenShift.
* **OpenShift Data Foundation (ODF/OCS):** (Recommended for Production) Provides enterprise-grade persistent, replicated storage for stateful services like MinIO, Kafka, and Milvus.

---

## GitOps with ArgoCD

This project is managed using a GitOps workflow powered by ArgoCD.
* **Kustomize:** Manifests for each component are organized into directories with `kustomization.yaml` files.
* **App of Apps Pattern:** A root ArgoCD Application (defined in `bootstrap/app-of-apps.yaml`) orchestrates the deployment of all other components by managing a set of child ArgoCD `Application` resources.

## Project Structure

<!--
The user should paste the output of the `tree -L 3` command here to show the project layout.
The provided tree structure will be formatted into this code block.
-->
```
.
├── apps
│   ├── api
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── minio-event-bridge
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── s3-event-handler
│       ├── app.py
│       ├── Dockerfile
│       └── requirements.txt
├── bootstrap
│   ├── app-event-bridge.yaml
│   ├── app-event-handler.yaml
│   ├── app-kafka.yaml
│   ├── app-knative-broker.yaml
│   ├── app-minio.yaml
│   ├── app-mock-api.yaml
│   ├── app-of-apps.yaml
│   └── kustomization.yaml
├── dsc.yaml
├── LICENSE
├── pipeline
│   ├── api-to-rag
│   │   └── api-pipeline.yaml
│   └── pdf-to-docling
│       ├── pdf-pipeline.py
│       ├── simple_pdf_pipeline.yaml
│       └── test2.pdf
├── README.md
├── scripts
│   ├── event-test-*.pdf
│   ├── example.env
│   ├── generate_and_upload_pdf.py
│   ├── setup_minio_events.sh
│   └── test-minio-pod.yaml
└── services
    ├── docling
    │   └── Containerfile
    ├── kafka
    │   ├── kafka.yaml
    │   ├── kustomization.yaml
    │   └── topic.yaml
    ├── knative
    │   ├── config.yaml
    │   ├── knative-kafka.yaml
    │   ├── knativeeventing.yaml
    │   ├── knativeserving.yaml
    │   ├── kustomization.yaml
    │   ├── networkpolicy.yaml
    │   ├── trigger.yaml
    │   └── wait-for-installation.yaml
    ├── milvus
    │   ├── config.yaml
    │   ├── deployment.yaml
    │   ├── kustomization.yaml
    │   ├── pvc.yaml
    │   └── service.yaml
    └── minio
        ├── create-secret.yaml
        ├── deployment.yaml
        ├── kustomization.yaml
        ├── namespace.yaml
        ├── network-policies.yaml
        ├── pvc.yaml
        ├── route.yaml
        ├── sa.yaml
        └── service.yaml
```

---

## III. Setup and Deployment (GitOps with ArgoCD)

### Prerequisites

* OpenShift Cluster (4.x).
* **Required Operators Installed (see list above).**
* Git repository hosting this project structure.
* OpenShift CLI (`oc`) installed locally.
* MinIO Client (`mc`) installed locally for initial bucket/event setup.
* (Optional) Podman/Docker for building images.

### Initial Setup Steps:

1.  **Clone the Repository & Customize Placeholders:**
    * Clone this Git repository.
    * Review all YAML files for placeholders like `YOUR_GIT_REPO_URL`, namespaces, image URLs, and secrets. Update them to match your environment.
    * Pay special attention to `services/minio/create-secret.yaml` and `services/minio/deployment.yaml` for MinIO credentials and Kafka notification settings.
    * Update `scripts/example.env` with your specific values. You can `source scripts/example.env` to set them in your shell.

2.  **ArgoCD Sync Waves & Hooks:**
    * The `KnativeServing` and `KnativeEventing` resources in `services/knative/` are configured with `argocd.argoproj.io/sync-wave: "1"`.
    * A Kubernetes Job manifest, `services/knative/wait-for-installation.yaml`, is included with the `argocd.argoproj.io/hook: Sync` annotation. This hook pauses the ArgoCD sync process until it detects that the core Knative CRDs (like `Broker` and `Trigger`) have been installed by the operator, preventing race-condition errors.
    * Subsequent resources like `KnativeKafka` and application-level Triggers are placed in later sync waves (`"2"`, `"3"`, etc.) to ensure dependencies are met.

3.  **Push Changes to Your Git Repository:** Commit all customized files.

4.  **Apply the Root ArgoCD Application:**
    ```bash
    # Apply to the namespace where ArgoCD is running (typically 'openshift-gitops')
    oc apply -f bootstrap/app-of-apps.yaml -n openshift-gitops
    ```
    ArgoCD will then deploy all components in the order defined by the sync waves.

5.  **Post-Deployment MinIO Configuration (Bucket and Event Notifications):**
    Once MinIO is deployed and its `Deployment` includes the Kafka environment variables:
    * **Run the `setup_minio_events.sh` script:** This configures your MinIO bucket to send PDF upload events to the Kafka target.
        ```bash
        # Ensure environment variables from scripts/example.env are set and sourced.
        cd scripts/
        chmod +x setup_minio_events.sh
        ./setup_minio_events.sh
        cd ..
        ```

6.  **Prepare and Upload Kubeflow Pipeline:**
    * Navigate to `pipeline/pdf-to-docling/`.
    * Compile the pipeline: `python pdf-pipeline.py` (generates `s3_pdf_docling_pipeline.yaml` or similar).
    * Upload the compiled pipeline file to your Kubeflow Pipelines UI. Note its "Display name".
    * Ensure the `KFP_PIPELINE_NAME` in the `s3-event-handler`'s Knative Service YAML matches this display name.

---

## IV. API Usage & Testing

### Mock API Endpoints

* **Health Check:** `GET /api/v1/health`
* **Incidents:** `GET /api/v1/incidents`, `POST /api/v1/incidents`, etc.
* **Service Requests:** `GET /api/v1/requests`, etc.

**Example `curl` for Mock API (replace `<mock-api-route>` with actual route URL):**

<!--
The user should paste an example `curl` command for their mock API here.
-->
```bash
curl http://<mock-api-route-for-your-api-project>/api/v1/incidents?limit=2 | jq
```

### Testing Event-Driven Flow

1.  **Verify `kfp-s3-trigger` Knative Service is READY:**
    ```bash
    oc get ksvc kfp-s3-trigger -n rag-pipeline-workshop
    # Expected output should show READY True
    ```
    Test its health endpoint using its OpenShift Route:
    ```bash
    curl -k https://<kfp-s3-trigger-route-url>/healthz
    ```

2.  **Upload a PDF file** to the MinIO bucket configured for notifications (e.g., `pdf-inbox`).

3.  **Monitor Logs & Status:**
    * **MinIO Pod(s):** (`minio` namespace) `oc logs -l app=minio -n minio -f`
    * **Kafka Consumer (Debug):** (`kafka` namespace, `minio-bucket-notifications` topic)
        ```bash
        oc exec -n kafka <your-kafka-broker-pod-name> -- bin/kafka-console-consumer.sh \
            --bootstrap-server localhost:9092 \
            --topic minio-bucket-notifications --from-beginning --timeout-ms 15000
        ```
    * **Knative KafkaSource Pod(s):** (`rag-pipeline-workshop` namespace) `oc logs -l eventing.knative.dev/source=<your-kafkasource-name> -c dispatcher -n rag-pipeline-workshop -f`
    * **`s3-event-handler` Knative Service Pod(s):** (`rag-pipeline-workshop` namespace) `oc logs -l serving.knative.dev/service=kfp-s3-trigger -c user-container -n rag-pipeline-workshop -f`

4.  **Check Kubeflow Pipelines UI:** Look for new pipeline runs in the "S3 Triggered PDF Runs" experiment.

---

## V. Troubleshooting

* **ArgoCD Sync Issues:** Check the ArgoCD UI for sync errors. If a wave is stuck, inspect the logs of the failing resource.
* **Pod Status:** Use `oc get pods`, `oc logs`, and `oc describe pod` in the relevant namespaces.
* **Knative Services (`ksvc`):** Use `oc get ksvc -n rag-pipeline-workshop` to check readiness conditions. If not ready, inspect the logs for the `kourier` pods in the `openshift-serverless` namespace and the `activator` / `autoscaler` pods in the `knative-serving` namespace.
* **Knative Eventing (Broker, Trigger, KafkaSource):** Use `oc get broker,trigger,kafkasource -n rag-pipeline-workshop` to check readiness and conditions.
* **KFP Integration:** Check `s3-event-handler` logs for errors connecting to the KFP endpoint. Ensure the `KFP_ENDPOINT` (internal service URL) and `KFP_PIPELINE_NAME` are correct, and that the `kfp-trigger-sa` ServiceAccount has the necessary RBAC permissions in the KFP namespace.

---

## Documentation

This project includes comprehensive lab guides built with Antora. To view the documentation locally:

### Viewing Lab Documentation
Run the Antora viewer container to render and serve the documentation:

```bash
podman run --rm --name antora -v $PWD:/antora -p 8080:8080 -i -t ghcr.io/juliaaano/antora-viewer
```

For SELinux environments, append `:z` to the volume mount:
```bash
podman run --rm --name antora -v $PWD:/antora:z -p 8080:8080 -i -t ghcr.io/juliaaano/antora-viewer
```

Once running, open your browser to [http://localhost:8080](http://localhost:8080) to view the rendered lab guides.

To stop the container:
```bash
podman stop antora
```

**Note:** Live-reload is not supported. Restart the container to see content changes.

---

## Manual Pipeline Triggering

you can manually trigger the pipeline as followed

Deploy debug pod first in minio namespace
`oc run debug-pod -n minio --image=curlimages/curl -- sh -c 'sleep infinity'`

Run a curl command from the debug pod that will then create an event in the minio-event-bridge service.
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