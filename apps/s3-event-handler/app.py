import os
import json
from flask import Flask, request, jsonify
from kfp import Client as KFPClient
from kfp.compiler import Compiler
from kfp.dsl import pipeline, ContainerOp # For potential future in-line compilation

# Environment Variables for KFP connection and S3 details
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT") # e.g., http://ds-pipeline-pipelines-definition.openshift-ai-project.svc.cluster.local:8888
KFP_BEARER_TOKEN = os.environ.get("KFP_BEARER_TOKEN") # SA token if not using in-cluster config or KFP_SA_TOKEN_PATH
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token" # For in-cluster auth

# Name of your pre-compiled/uploaded Kubeflow Pipeline and its version (if applicable)
# For this example, we'll assume the pipeline is uploaded to KFP UI beforehand.
# Alternatively, one could compile and upload on the fly, but that adds complexity.
PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "Simple PDF Processing Pipeline")
# If you upload versions of your pipeline, specify one.
# PIPELINE_VERSION_ID = os.environ.get("KFP_PIPELINE_VERSION_ID")

# Kubeflow Pipelines Experiment to run under
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")

app = Flask(__name__)

def get_kfp_client():
    """Initializes and returns a KFP client."""
    if KFP_ENDPOINT:
        if os.path.exists(KFP_SA_TOKEN_PATH):
            print(f"KFP Client: Using in-cluster SA token from {KFP_SA_TOKEN_PATH} for endpoint {KFP_ENDPOINT}")
            # For KFP SDK, if the endpoint is in-cluster and you have RBAC,
            # often you don't need to explicitly pass the token if KFP is configured for it.
            # However, some KFP setups might require an explicit bearer token.
            # The KFP SDK's Client() without arguments tries to load in-cluster config.
            # If your KFP instance requires a bearer token explicitly:
            with open(KFP_SA_TOKEN_PATH, 'r') as f:
                token = f.read()
            credentials = KFPClient(host=KFP_ENDPOINT).set_user_credentials(user_token=token)
            return KFPClient(host=KFP_ENDPOINT, existing_credentials=credentials) # Simplified if token is automatically picked up
        elif KFP_BEARER_TOKEN:
            print(f"KFP Client: Using KFP_BEARER_TOKEN for endpoint {KFP_ENDPOINT}")
            credentials = KFPClient(host=KFP_ENDPOINT).set_user_credentials(user_token=KFP_BEARER_TOKEN)
            return KFPClient(host=KFP_ENDPOINT, existing_credentials=credentials)
        else:
            # This attempts to load config from .kube/config or in-cluster config
            # May or may not work depending on KFP auth setup and where this code runs
            print(f"KFP Client: Trying default auth for endpoint {KFP_ENDPOINT}")
            return KFPClient(host=KFP_ENDPOINT)
    else:
        # Fallback for local testing or if KFP_ENDPOINT is not set
        # Tries to load from ~/.config/kfp/context.json or in-cluster config if available
        print("KFP Client: KFP_ENDPOINT not set, trying default client initialization.")
        return KFPClient()


@app.route('/', methods=['POST'])
def handle_s3_event():
    """
    Receives an S3 event (CloudEvent format from Knative) and triggers a KFP.
    """
    if not KFP_ENDPOINT:
        print("Error: KFP_ENDPOINT environment variable not set.")
        return jsonify({"error": "KFP endpoint not configured"}), 500

    try:
        # Knative Eventing typically delivers events as CloudEvents
        # The actual S3 event details are in the 'data' field of the CloudEvent
        cloudevent = request.json
        app.logger.info(f"Received CloudEvent: {json.dumps(cloudevent, indent=2)}")

        s3_event_data = cloudevent.get('data') # This structure depends on the S3 event source
        if not s3_event_data:
            # Try to get data directly if not nested (e.g. if S3Source sends raw S3 event)
            s3_event_data = cloudevent

        # Standard S3 event structure might have 'Records'
        # AWS S3 Event Structure (often adapted by compatible sources)
        if 'Records' in s3_event_data:
            record = s3_event_data['Records'][0]
            bucket_name = record['s3']['bucket']['name']
            object_key = record['s3']['object']['key']
        elif 'bucket' in s3_event_data and 'key' in s3_event_data: # Simpler custom event
            bucket_name = s3_event_data['bucket']
            object_key = s3_event_data['key']
        else:
            app.logger.error(f"Could not parse S3 details from event: {s3_event_data}")
            return jsonify({"error": "Could not parse S3 bucket/key from event"}), 400

        app.logger.info(f"Processing S3 event for: bucket='{bucket_name}', key='{object_key}'")

        if not object_key.lower().endswith(".pdf"):
            app.logger.info(f"Object '{object_key}' is not a PDF. Skipping KFP trigger.")
            return jsonify({"message": "Object is not a PDF, pipeline not triggered"}), 200

        # Initialize KFP client
        client = get_kfp_client()

        # Find the pipeline ID from its name
        # Note: This API call can be slow. Better to use pipeline ID directly if known or version ID.
        pipeline_info = client.get_pipeline_id(PIPELINE_NAME)
        if not pipeline_info:
            app.logger.error(f"Pipeline with name '{PIPELINE_NAME}' not found in KFP.")
            return jsonify({"error": f"Pipeline '{PIPELINE_NAME}' not found"}), 500
        pipeline_id = pipeline_info.id
        app.logger.info(f"Found KFP Pipeline ID: {pipeline_id} for name '{PIPELINE_NAME}'")

        # Define pipeline parameters
        pipeline_params = {
            'pdf_s3_bucket': bucket_name,
            'pdf_s3_key': object_key,
            'processing_timestamp': cloudevent.get('time', '') # Pass event time
        }

        # Get or create an experiment
        try:
            experiment = client.get_experiment(experiment_name=KFP_EXPERIMENT_NAME)
        except Exception: # Catch more specific KFP exceptions if possible
            app.logger.info(f"Experiment '{KFP_EXPERIMENT_NAME}' not found. Creating it.")
            experiment = client.create_experiment(name=KFP_EXPERIMENT_NAME)
        app.logger.info(f"Using KFP Experiment ID: {experiment.id}")

        # Run the pipeline
        run_name = f"{PIPELINE_NAME} Run - {object_key.split('/')[-1]} - {cloudevent.get('id', 'unknown_event')}"
        # Use pipeline_id for more robust triggering
        run_result = client.run_pipeline(
            experiment_id=experiment.id,
            job_name=run_name, # Also known as run_name
            pipeline_id=pipeline_id, # Use if you have uploaded the pipeline
            # pipeline_package_path=None, # Use if compiling/uploading on the fly
            params=pipeline_params
        )

        app.logger.info(f"Successfully triggered KFP run: {run_result.id} - {run_result.name}")
        return jsonify({
            "message": "Kubeflow Pipeline triggered successfully",
            "kfp_run_id": run_result.id,
            "kfp_run_name": run_result.name,
            "kfp_run_url": f"{KFP_ENDPOINT}/#/runs/details/{run_result.id}" # Generic URL, might need adjustment
        }), 200

    except Exception as e:
        app.logger.error(f"Error processing S3 event or triggering KFP: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False) # Debug False for production