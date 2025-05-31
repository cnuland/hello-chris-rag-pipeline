import os
import json
import uuid
import logging
import sys
from datetime import datetime # Added missing import for datetime.utcnow()
from flask import Flask, request, jsonify, g
from kfp import Client as KFPClient
# from kfp.compiler import Compiler # Not used in this runtime app
# from kfp.dsl import pipeline, ContainerOp # Not used in this runtime app

# Environment Variables for KFP connection and S3 details
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
KFP_BEARER_TOKEN = os.environ.get("KFP_BEARER_TOKEN")
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "Simple PDF Processing Pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

class RequestFormatter(logging.Formatter):
    def format(self, record):
        record.request_id = getattr(g, 'request_id', 'no-request-id')
        return super().format(record)

root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)
if root_logger.handlers:
    root_logger.handlers.clear()
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(LOG_LEVEL)
formatter = RequestFormatter('%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s - %(module)s:%(lineno)d') # Added module/lineno
handler.setFormatter(formatter)
root_logger.addHandler(handler)

app = Flask(__name__)
app.logger.handlers = root_logger.handlers
app.logger.setLevel(LOG_LEVEL) # Ensure Flask's logger also uses the set level

@app.before_request
def before_request_logging():
    g.request_id = str(uuid.uuid4())
    app.logger.debug(f"Request received: {request.method} {request.path} from {request.remote_addr}")
    # Limiting header logging for brevity unless debug is very high
    if app.logger.isEnabledFor(logging.DEBUG): 
        app.logger.debug(f"Request headers: {dict(request.headers)}")

@app.after_request
def after_request_logging(response):
    app.logger.debug(f"Response status: {response.status_code}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client."""
    if KFP_ENDPOINT:
        if os.path.exists(KFP_SA_TOKEN_PATH):
            app.logger.info(f"KFP Client: Using in-cluster SA token from {KFP_SA_TOKEN_PATH} for endpoint {KFP_ENDPOINT}")
            try:
                with open(KFP_SA_TOKEN_PATH, 'r') as f:
                    token = f.read().strip() # Added strip()
                app.logger.debug("Successfully read token from service account path")
                # For KFP SDK >= 2.0, direct token passing might be different or client handles it.
                # For older KFP SDK (like 1.8.x), set_user_credentials was common.
                # Let's assume a simple client init and rely on env vars or auto-config for in-cluster.
                # If using kfp.Client(), it might try to auto-configure.
                # If explicit token is needed:
                # client = KFPClient(host=KFP_ENDPOINT, existing_token=token) 
                client = KFPClient(host=KFP_ENDPOINT) # Simpler, tries to auto-configure
                # To test connection: client.list_experiments(page_size=1) 
                return client
            except Exception as e:
                app.logger.error(f"Failed to configure KFP client with SA token: {e}", exc_info=True)
                raise
        elif KFP_BEARER_TOKEN:
            app.logger.info(f"KFP Client: Using KFP_BEARER_TOKEN for endpoint {KFP_ENDPOINT}")
            # client = KFPClient(host=KFP_ENDPOINT, existing_token=KFP_BEARER_TOKEN)
            client = KFPClient(host=KFP_ENDPOINT) # Simpler, relies on SDK to use token if needed
            return client
        else:
            app.logger.info(f"KFP Client: Trying default auth for endpoint {KFP_ENDPOINT}")
            return KFPClient(host=KFP_ENDPOINT)
    else:
        app.logger.warning("KFP Client: KFP_ENDPOINT not set. KFP functionality will be disabled.")
        return None # Return None if no endpoint, to be handled later

@app.route('/healthz', methods=['GET'])
def healthz():
    app.logger.debug("Health check request received")
    return jsonify(status="healthy", message="Application is running"), 200
    
@app.route('/', methods=['POST'])
def handle_s3_event():
    app.logger.info("==== NEW EVENT RECEIVED AT / ====")
    
    app.logger.debug(f"Request content type: {request.content_type}")
    app.logger.debug(f"Request size: {request.content_length} bytes")
    
    cloudevent_headers = {
        k.lower(): v for k, v in request.headers.items() 
        if k.lower().startswith('ce-')
    }
    app.logger.info(f"CloudEvent-like headers: {json.dumps(cloudevent_headers, indent=2)}")
    
    try:
        cloudevent = request.json
        if cloudevent is None and request.data: # Handle cases where content-type might be text/plain but is json
            app.logger.warning("Request not parsed as JSON, but data present. Trying to parse data as JSON.")
            try:
                cloudevent = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError as e:
                app.logger.error(f"Failed to parse request.data as JSON: {e}. Raw data (first 500 chars): {request.data[:500]}")
                return jsonify({"error": "Invalid request format - expected JSON in body or data"}), 400
        
        if cloudevent is None:
            app.logger.error("Request body is empty or not valid JSON, and request.data is also empty/unparsable.")
            return jsonify({"error": "Request body is empty or not valid JSON"}), 400
            
        app.logger.info(f"Full Received Event Body (parsed as JSON): {json.dumps(cloudevent, indent=2)}")
        
        # ---- MODIFICATION TO "ACCEPT EVERYTHING" FOR DEBUGGING ----
        # We will log the event and then try to process it as if it's the expected MinIO event
        # but we won't strictly enforce PDF or specific EventName for now, just log.
        
        app.logger.info("Attempting to process event as S3 notification wrapped in CloudEvent...")

        # Extract standard CloudEvent attributes for logging
        ce_id = request.headers.get('Ce-Id', cloudevent.get('id', 'N/A'))
        ce_source = request.headers.get('Ce-Source', cloudevent.get('source', 'N/A'))
        ce_type = request.headers.get('Ce-Type', cloudevent.get('type', 'N/A'))
        ce_time = request.headers.get('Ce-Time', cloudevent.get('time', datetime.utcnow().isoformat() + "Z"))
        
        app.logger.info(f"Parsed CloudEvent attributes: id={ce_id}, source={ce_source}, type={ce_type}, time={ce_time}")

        minio_event_data = cloudevent.get('data')
        if not minio_event_data:
            app.logger.warning("No 'data' field in CloudEvent. Assuming entire payload is the MinIO event.")
            minio_event_data = cloudevent # Use the whole event as MinIO data

        bucket_name = "unknown_bucket"
        object_key = "unknown_key"
        eventName = "unknown_eventName"
        is_pdf = False

        try:
            # Try to parse MinIO event structure from minio_event_data
            if 'Records' in minio_event_data and len(minio_event_data['Records']) > 0:
                record = minio_event_data['Records'][0]
                eventName = record.get('eventName', 'unknown_eventName_in_records')
                s3_info = record.get('s3', {})
                object_info = s3_info.get('object', {})
                bucket_info = s3_info.get('bucket', {})
                object_key = object_info.get('key', 'unknown_key_in_records')
                bucket_name = bucket_info.get('name', 'unknown_bucket_in_records')
            elif 'Key' in minio_event_data and 'Bucket' in minio_event_data : # Simpler MinIO like structure sometimes seen
                 object_key = minio_event_data.get('Key')
                 bucket_name = minio_event_data.get('Bucket')
                 eventName = minio_event_data.get('EventName', 's3:ObjectCreated:Put') # Assume create if not present
            
            app.logger.info(f"Interpreted MinIO event: Name='{eventName}', Bucket='{bucket_name}', Key='{object_key}'")
            
            if object_key and isinstance(object_key, str):
                is_pdf = object_key.lower().endswith(".pdf")
                app.logger.info(f"Is PDF: {is_pdf}")
            else:
                app.logger.warning(f"Object key '{object_key}' is not a valid string or is missing.")

        except Exception as parse_ex:
            app.logger.error(f"Error parsing S3 details from event data: {parse_ex}", exc_info=True)
            # Continue for debugging, don't return error yet

        # For debugging, we will proceed to log KFP attempt even if not PDF or wrong event type
        app.logger.info(f"DEBUG: Proceeding to KFP logic regardless of PDF/event type check for event: {object_key}")

        if not KFP_ENDPOINT:
            app.logger.error("KFP_ENDPOINT not set. Cannot trigger pipeline.")
            return jsonify({"error": "KFP endpoint not configured on server"}), 500
        
        kfp_client = get_kfp_client()
        if not kfp_client:
            app.logger.error("KFP client could not be initialized. Cannot trigger pipeline.")
            return jsonify({"error": "KFP client initialization failed"}), 500

        try:
            app.logger.info(f"Looking for pipeline: '{PIPELINE_NAME}'")
            pipeline_info = kfp_client.get_pipeline_id(PIPELINE_NAME)
            if not pipeline_info:
                app.logger.error(f"Pipeline '{PIPELINE_NAME}' not found.")
                return jsonify({"error": f"Pipeline '{PIPELINE_NAME}' not found"}), 500
            pipeline_id = pipeline_info.id
            app.logger.info(f"Found KFP Pipeline ID: {pipeline_id}")

            experiment = kfp_client.get_experiment(experiment_name=KFP_EXPERIMENT_NAME)
            app.logger.info(f"Using KFP Experiment ID: {experiment.id} (Name: {KFP_EXPERIMENT_NAME})")

            pipeline_params = {
                'pdf_s3_bucket': bucket_name if bucket_name else "unknown",
                'pdf_s3_key': object_key if object_key else "unknown",
                'processing_timestamp': ce_time,
            }
            app.logger.info(f"Pipeline parameters for KFP: {json.dumps(pipeline_params)}")

            run_name = f"PDF Event - {object_key if object_key else 'unknown_key'} - {ce_id}"
            run_result = kfp_client.run_pipeline(
                experiment_id=experiment.id,
                job_name=run_name,
                pipeline_id=pipeline_id,
                params=pipeline_params
            )
            run_url = f"{KFP_ENDPOINT}/#/runs/details/{run_result.id}"
            app.logger.info(f"KFP run successfully triggered: ID={run_result.id}, Name='{run_result.name}', URL: {run_url}")
            
            return jsonify({
                "message": "KFP pipeline triggered (or attempted for debugging)",
                "event_received": True,
                "parsed_bucket": bucket_name,
                "parsed_key": object_key,
                "is_pdf_detected": is_pdf,
                "kfp_run_id": run_result.id,
                "kfp_run_url": run_url
            }), 200

        except Exception as kfp_ex:
            app.logger.error(f"Error during KFP interaction: {kfp_ex}", exc_info=True)
            return jsonify({"error": f"KFP interaction failed: {str(kfp_ex)}"}), 500

    except Exception as e:
        app.logger.error(f"Generic error in event handler: {e}", exc_info=True)
        return jsonify({"error": "Internal server error processing event"}), 500

if __name__ == '__main__': # This block is mainly for local testing, Gunicorn runs app in container
    port = int(os.environ.get("PORT", 8080))
    # For local testing, Flask's built-in server with debug can be useful
    # Set FLASK_DEBUG=true as an environment variable for local debug mode
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    app.logger.info(f"=== Starting S3 Event Handler (Local Test Mode: {debug_mode}) ===")
    app.logger.info(f"Log level: {LOG_LEVEL}")
    app.logger.info(f"KFP Endpoint: {KFP_ENDPOINT}")
    # ... other startup logs
    app.run(host='0.0.0.0', port=port, debug=debug_mode)