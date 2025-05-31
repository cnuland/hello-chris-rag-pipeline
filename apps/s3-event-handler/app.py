import os
import json
import uuid
import logging
import sys
from datetime import datetime
from flask import Flask, request, jsonify, g
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

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Configure the logger
class RequestFormatter(logging.Formatter):
    def format(self, record):
        if hasattr(g, 'request_id'):
            record.request_id = g.request_id
        else:
            record.request_id = 'no-request-id'
        return super().format(record)

# Set up root logger
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)

# Clear any existing handlers to avoid duplication
if root_logger.handlers:
    root_logger.handlers.clear()

# Create a handler that outputs to stdout
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(LOG_LEVEL)

# Format with timestamp, level, request ID, and message
formatter = RequestFormatter('%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s')
handler.setFormatter(formatter)
root_logger.addHandler(handler)

# Set Flask logger to use the same configuration
app = Flask(__name__)
app.logger.handlers = root_logger.handlers
app.logger.setLevel(LOG_LEVEL)

# Request middleware to add request ID and log request details
@app.before_request
def before_request():
    g.request_id = str(uuid.uuid4())
    app.logger.debug(f"Request received: {request.method} {request.path}")
    app.logger.debug(f"Request headers: {dict(request.headers)}")
    app.logger.debug(f"Request remote addr: {request.remote_addr}")

@app.after_request
def after_request(response):
    app.logger.debug(f"Response status: {response.status_code}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client."""
    if KFP_ENDPOINT:
        if os.path.exists(KFP_SA_TOKEN_PATH):
            app.logger.info(f"KFP Client: Using in-cluster SA token from {KFP_SA_TOKEN_PATH} for endpoint {KFP_ENDPOINT}")
            # For KFP SDK, if the endpoint is in-cluster and you have RBAC,
            # often you don't need to explicitly pass the token if KFP is configured for it.
            # However, some KFP setups might require an explicit bearer token.
            # The KFP SDK's Client() without arguments tries to load in-cluster config.
            # If your KFP instance requires a bearer token explicitly:
            try:
                with open(KFP_SA_TOKEN_PATH, 'r') as f:
                    token = f.read()
                app.logger.debug("Successfully read token from service account path")
                credentials = KFPClient(host=KFP_ENDPOINT).set_user_credentials(user_token=token)
                return KFPClient(host=KFP_ENDPOINT, existing_credentials=credentials) # Simplified if token is automatically picked up
            except Exception as e:
                app.logger.error(f"Failed to read token from {KFP_SA_TOKEN_PATH}: {e}", exc_info=True)
                raise
        elif KFP_BEARER_TOKEN:
            app.logger.info(f"KFP Client: Using KFP_BEARER_TOKEN for endpoint {KFP_ENDPOINT}")
            credentials = KFPClient(host=KFP_ENDPOINT).set_user_credentials(user_token=KFP_BEARER_TOKEN)
            return KFPClient(host=KFP_ENDPOINT, existing_credentials=credentials)
        else:
            # This attempts to load config from .kube/config or in-cluster config
            # May or may not work depending on KFP auth setup and where this code runs
            app.logger.info(f"KFP Client: Trying default auth for endpoint {KFP_ENDPOINT}")
            return KFPClient(host=KFP_ENDPOINT)
    else:
        # Fallback for local testing or if KFP_ENDPOINT is not set
        # Tries to load from ~/.config/kfp/context.json or in-cluster config if available
        app.logger.warning("KFP Client: KFP_ENDPOINT not set, trying default client initialization.")
        return KFPClient()

@app.route('/healthz', methods=['GET']) # Ensure this route exists
def healthz():
    """Dedicated health check endpoint."""
    app.logger.debug("Health check request received")
    return jsonify(status="healthy", message="Application is running"), 200
    
@app.route('/', methods=['POST'])
def handle_s3_event():
    """
    Receives an S3 event (CloudEvent format from Knative) and triggers a KFP.
    """
    app.logger.info("==== NEW S3 EVENT RECEIVED ====")
    
    # Log the raw request data for debugging
    app.logger.debug(f"Request content type: {request.content_type}")
    app.logger.debug(f"Request size: {request.content_length} bytes")
    
    # Log CloudEvent specific headers if present
    cloudevent_headers = {
        k: v for k, v in request.headers.items() 
        if k.lower().startswith('ce-') or k.lower() in ['content-type']
    }
    app.logger.info(f"CloudEvent headers: {json.dumps(cloudevent_headers, indent=2)}")
    
    if not KFP_ENDPOINT:
        app.logger.error("KFP_ENDPOINT environment variable not set.")
        return jsonify({"error": "KFP endpoint not configured"}), 500

    try:
        # Check if the request has JSON content
        if not request.is_json:
            app.logger.warning(f"Received non-JSON request with content-type: {request.content_type}")
            try:
                raw_data = request.get_data().decode('utf-8')
                app.logger.debug(f"Raw request data: {raw_data[:1000]}...")  # Log first 1000 chars
                # Try to parse as JSON anyway
                cloudevent = json.loads(raw_data)
            except json.JSONDecodeError as e:
                app.logger.error(f"Failed to parse request data as JSON: {e}")
                return jsonify({"error": "Invalid request format - expected JSON"}), 400
        else:
            cloudevent = request.json
        
        # Log the full CloudEvent for debugging
        app.logger.info(f"Received CloudEvent: {json.dumps(cloudevent, indent=2)}")
        
        # Log CloudEvent attributes for tracing
        ce_id = cloudevent.get('id', 'unknown')
        ce_source = cloudevent.get('source', 'unknown')
        ce_type = cloudevent.get('type', 'unknown')
        ce_time = cloudevent.get('time', datetime.utcnow().isoformat())
        
        app.logger.info(f"Processing CloudEvent: id={ce_id}, source={ce_source}, type={ce_type}, time={ce_time}")
        
        # Extract S3 event data from CloudEvent
        s3_event_data = cloudevent.get('data')  # This structure depends on the S3 event source
        if not s3_event_data:
            app.logger.warning("No 'data' field found in CloudEvent, trying to use event as raw S3 event")
            # Try to get data directly if not nested (e.g. if S3Source sends raw S3 event)
            s3_event_data = cloudevent
        
        app.logger.debug(f"S3 event data structure: {json.dumps(s3_event_data, indent=2)}")
        
        # Standard S3 event structure might have 'Records'
        # AWS S3 Event Structure (often adapted by compatible sources)
        if 'Records' in s3_event_data:
            app.logger.debug("Found AWS S3 event structure with 'Records' field")
            record = s3_event_data['Records'][0]
            event_name = record.get('eventName', 'unknown')
            event_time = record.get('eventTime', 'unknown')
            bucket_name = record['s3']['bucket']['name']
            object_key = record['s3']['object']['key']
            object_size = record['s3']['object'].get('size', 'unknown')
            app.logger.info(f"AWS S3 event details: event={event_name}, time={event_time}, size={object_size}")
        elif 'bucket' in s3_event_data and 'key' in s3_event_data:  # Simpler custom event
            app.logger.debug("Found simplified S3 event structure with direct 'bucket' and 'key' fields")
            bucket_name = s3_event_data['bucket']
            object_key = s3_event_data['key']
        elif 'Key' in s3_event_data and 'Bucket' in s3_event_data:  # MinIO specific event format
            app.logger.debug("Found possible MinIO event structure with 'Key' and 'Bucket' fields")
            bucket_name = s3_event_data['Bucket']
            object_key = s3_event_data['Key']
        else:
            app.logger.error(f"Could not parse S3 details from event structure: {json.dumps(s3_event_data, indent=2)}")
            return jsonify({"error": "Could not parse S3 bucket/key from event"}), 400

        app.logger.info(f"Processing S3 event for: bucket='{bucket_name}', key='{object_key}'")

        # Check if the object is a PDF file
        if not object_key.lower().endswith(".pdf"):
            app.logger.info(f"Object '{object_key}' is not a PDF. Skipping KFP trigger.")
            return jsonify({
                "message": "Object is not a PDF, pipeline not triggered",
                "request_id": g.request_id,
                "bucket": bucket_name,
                "key": object_key
            }), 200

        # Initialize KFP client
        app.logger.debug("Initializing KFP client")
        try:
            client = get_kfp_client()
            app.logger.debug("KFP client initialized successfully")
        except Exception as e:
            app.logger.error(f"Failed to initialize KFP client: {e}", exc_info=True)
            return jsonify({
                "error": f"Failed to initialize KFP client: {str(e)}",
                "request_id": g.request_id
            }), 500

        # Find the pipeline ID from its name
        # Note: This API call can be slow. Better to use pipeline ID directly if known or version ID.
        app.logger.debug(f"Looking for pipeline with name: '{PIPELINE_NAME}'")
        try:
            pipeline_info = client.get_pipeline_id(PIPELINE_NAME)
            if not pipeline_info:
                app.logger.error(f"Pipeline with name '{PIPELINE_NAME}' not found in KFP.")
                return jsonify({
                    "error": f"Pipeline '{PIPELINE_NAME}' not found",
                    "request_id": g.request_id
                }), 500
            pipeline_id = pipeline_info.id
            app.logger.info(f"Found KFP Pipeline ID: {pipeline_id} for name '{PIPELINE_NAME}'")

        except Exception as e:
            app.logger.error(f"Failed to retrieve pipeline info: {e}", exc_info=True)
            return jsonify({
                "error": f"Failed to retrieve pipeline: {str(e)}",
                "request_id": g.request_id
            }), 500

        # Define pipeline parameters
        pipeline_params = {
            'pdf_s3_bucket': bucket_name,
            'pdf_s3_key': object_key,
            'processing_timestamp': cloudevent.get('time', ''),  # Pass event time
            'cloud_event_id': cloudevent.get('id', '')  # Pass CloudEvent ID for tracing
        }
        app.logger.debug(f"Pipeline parameters: {json.dumps(pipeline_params, indent=2)}")

        # Get or create an experiment
        app.logger.debug(f"Getting or creating experiment: '{KFP_EXPERIMENT_NAME}'")
        try:
            experiment = client.get_experiment(experiment_name=KFP_EXPERIMENT_NAME)
            app.logger.debug(f"Found existing experiment: {KFP_EXPERIMENT_NAME}")
        except Exception as e:  # Catch more specific KFP exceptions if possible
            app.logger.info(f"Experiment '{KFP_EXPERIMENT_NAME}' not found. Creating it. Error: {e}")
            try:
                experiment = client.create_experiment(name=KFP_EXPERIMENT_NAME)
                app.logger.info(f"Created new experiment: {KFP_EXPERIMENT_NAME}")
            except Exception as e:
                app.logger.error(f"Failed to create experiment: {e}", exc_info=True)
                return jsonify({
                    "error": f"Failed to create experiment: {str(e)}",
                    "request_id": g.request_id
                }), 500
        app.logger.info(f"Using KFP Experiment ID: {experiment.id}")

        # Run the pipeline
        filename = object_key.split('/')[-1]
        cloud_event_id = cloudevent.get('id', 'unknown_event')
        run_name = f"{PIPELINE_NAME} Run - {filename} - {cloud_event_id}"
        app.logger.info(f"Triggering pipeline run: '{run_name}'")
        
        try:
            # Use pipeline_id for more robust triggering
            run_result = client.run_pipeline(
                experiment_id=experiment.id,
                job_name=run_name,  # Also known as run_name
                pipeline_id=pipeline_id,  # Use if you have uploaded the pipeline
                # pipeline_package_path=None, # Use if compiling/uploading on the fly
                params=pipeline_params
            )

            app.logger.info(f"Successfully triggered KFP run: {run_result.id} - {run_result.name}")
            
            # Create pipeline run URL
            run_url = f"{KFP_ENDPOINT}/#/runs/details/{run_result.id}"  # Generic URL, might need adjustment
            app.logger.info(f"Pipeline run URL: {run_url}")
            
            return jsonify({
                "message": "Kubeflow Pipeline triggered successfully",
                "request_id": g.request_id,
                "cloud_event_id": cloud_event_id,
                "bucket": bucket_name,
                "key": object_key,
                "kfp_run_id": run_result.id,
                "kfp_run_name": run_result.name,
                "kfp_run_url": run_url
            }), 200
            
        except Exception as e:
            app.logger.error(f"Failed to run pipeline: {e}", exc_info=True)
            return jsonify({
                "error": f"Failed to run pipeline: {str(e)}",
                "request_id": g.request_id,
                "bucket": bucket_name,
                "key": object_key
            }), 500

    except json.JSONDecodeError as e:
        app.logger.error(f"JSON parsing error: {e}", exc_info=True)
        return jsonify({
            "error": f"Invalid JSON format: {str(e)}",
            "request_id": g.request_id
        }), 400
    except KeyError as e:
        app.logger.error(f"Missing required field in event: {e}", exc_info=True)
        return jsonify({
            "error": f"Missing required field: {str(e)}",
            "request_id": g.request_id
        }), 400
    except Exception as e:
        app.logger.error(f"Error processing S3 event or triggering KFP: {e}", exc_info=True)
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "request_id": g.request_id
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    app.logger.info(f"=== Starting S3 Event Handler ===")
    app.logger.info(f"Log level: {LOG_LEVEL}")
    app.logger.info(f"KFP Endpoint: {KFP_ENDPOINT}")
    app.logger.info(f"KFP Pipeline Name: {PIPELINE_NAME}")
    app.logger.info(f"KFP Experiment Name: {KFP_EXPERIMENT_NAME}")
    app.logger.info(f"Server running on port: {port}")
    app.logger.info(f"Debug mode: {debug_mode}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)  # Debug False for production
