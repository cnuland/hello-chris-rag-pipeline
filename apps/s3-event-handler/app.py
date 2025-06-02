import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, g
from kfp import Client as KFPClient # Keep KFP client import

# Environment Variables
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
KFP_BEARER_TOKEN = os.environ.get("KFP_BEARER_TOKEN") # May not be used if SA token is preferred
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "Simple PDF Processing Pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper() # Default to DEBUG for this phase

class RequestFormatter(logging.Formatter):
    def format(self, record):
        record.request_id = getattr(g, 'request_id', 'no-request-id')
        return super().format(record)

root_logger = logging.getLogger()
try:
    effective_log_level = getattr(logging, LOG_LEVEL)
except AttributeError:
    print(f"Warning: Invalid LOG_LEVEL '{LOG_LEVEL}'. Defaulting to DEBUG.", file=sys.stderr)
    effective_log_level = logging.DEBUG # Default to DEBUG
root_logger.setLevel(effective_log_level)

if root_logger.handlers:
    root_logger.handlers.clear()
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(effective_log_level)
formatter = RequestFormatter('%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s - %(module)s:%(lineno)d')
handler.setFormatter(formatter)
root_logger.addHandler(handler)

app = Flask(__name__)
app.logger.handlers = root_logger.handlers
app.logger.setLevel(effective_log_level)
gunicorn_error_logger = logging.getLogger('gunicorn.error')
gunicorn_error_logger.handlers = root_logger.handlers
gunicorn_error_logger.setLevel(effective_log_level)
gunicorn_access_logger = logging.getLogger('gunicorn.access')
gunicorn_access_logger.handlers = root_logger.handlers
gunicorn_access_logger.setLevel(effective_log_level)

@app.before_request
def before_request_logging():
    g.request_id = str(uuid.uuid4())
    app.logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG):
        app.logger.debug(f"RID-{g.request_id}: Request headers: {json.dumps(dict(request.headers), indent=2)}")

@app.after_request
def after_request_logging(response):
    request_id = getattr(g, 'request_id', 'no-request-id')
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, Response data (first 200): {response.get_data(as_text=True)[:200]}")
    return response

def get_kfp_client():
    if not KFP_ENDPOINT:
        app.logger.error("KFP_ENDPOINT environment variable not set. Cannot initialize KFP client.")
        return None

    # Validate KFP_ENDPOINT format
    app.logger.info(f"Validating KFP endpoint: {KFP_ENDPOINT}")
    if not KFP_ENDPOINT.startswith(('http://', 'https://')):
        app.logger.error(f"Invalid KFP_ENDPOINT format. Must start with http:// or https://")
        return None

    app.logger.info(f"Attempting to initialize KFP Client for endpoint: {KFP_ENDPOINT}")
    try:
        # Try to read the service account token if available
        token = None
        if os.path.exists(KFP_SA_TOKEN_PATH):
            try:
                with open(KFP_SA_TOKEN_PATH, 'r') as f:
                    token = f.read().strip()
                app.logger.debug("Successfully read service account token")
            except Exception as token_err:
                app.logger.warning(f"Could not read service account token: {token_err}")

        # Initialize the client with the exact KFP_ENDPOINT
        client = KFPClient(host=KFP_ENDPOINT)
        
        # Verify connection by making a simple API call
        try:
            client.get_pipeline_id("test-connection")
            app.logger.info("Successfully verified KFP API connection")
        except Exception as verify_err:
            app.logger.warning(f"KFP client created but API verification failed (this is not fatal): {verify_err}")

        return client

    except Exception as e:
        app.logger.error(f"Failed to initialize KFP client: {str(e)}", exc_info=True)
        app.logger.error(f"KFP_ENDPOINT used: {KFP_ENDPOINT}")
        if token:
            app.logger.debug("Service account token was available")
        return None

@app.route('/healthz', methods=['GET'])
def healthz():
    app.logger.debug("Health check request received at /healthz")
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200
    
@app.route('/', methods=['POST'])
def handle_s3_event():
    request_id = getattr(g, 'request_id', "NO_REQUEST_ID_IN_G") # Fallback for request_id
    app.logger.info(f"RID-{request_id}: ==== POST / request received by KFP-S3-TRIGGER user-container ====")
    
    raw_body_sample = "N/A"
    try:
        raw_body_sample = request.get_data(as_text=True)[:1000] # Get raw body as text (sample)
        app.logger.info(f"RID-{request_id}: Raw request body (first 1000 chars): {raw_body_sample}")
    except Exception as e_raw:
        app.logger.error(f"RID-{request_id}: Could not get raw request body: {e_raw}")

    app.logger.info(f"RID-{request_id}: Request Content-Type: {request.content_type}")
    cloudevent_headers = { k.lower(): v for k, v in request.headers.items() if k.lower().startswith('ce-')}
    app.logger.info(f"RID-{request_id}: Received CloudEvent-like headers: {json.dumps(cloudevent_headers, indent=2)}")
    
    cloudevent_payload = None
    try:
        # Knative dispatcher should send structured CloudEvents as application/cloudevents+json
        # or binary mode with ce-* headers and data as body with its original content type.
        # If it's binary, request.json might not work directly if Content-Type isn't application/json
        if "application/cloudevents+json" in request.content_type:
            cloudevent_payload = request.json
            app.logger.info(f"RID-{request_id}: Parsed structured CloudEvent from JSON body.")
        elif request.data and cloudevent_headers.get("ce-specversion"): # Likely binary mode
            app.logger.info(f"RID-{request_id}: Detected binary CloudEvent mode based on ce- headers and data.")
            # Reconstruct the CloudEvent object from headers and data
            # For binary mode, the data is the direct body, headers contain attributes.
            # The cloudevents SDK doesn't have a simple from_http_binary(headers, data)
            # We typically parse the data based on its original content-type (from ce-datacontenttype or Content-Type)
            # and then populate a CloudEvent object.
            # For now, let's assume the dispatcher sends the full CE as JSON even if it consumed binary from Kafka.
            # This is a common behavior for simplicity in dispatchers.
            # If not, this parsing will be more complex.
            # We'll try parsing request.data as JSON if it's not already parsed by request.json
            if not request.is_json and request.data:
                 cloudevent_payload = json.loads(request.data.decode('utf-8'))
                 app.logger.info(f"RID-{request_id}: Parsed request.data as JSON for potential CloudEvent structure.")
            else: # Should have been parsed by request.json if CE structured
                cloudevent_payload = request.json
        else: # Fallback, try to parse data as JSON if content-type was just application/json
            if request.is_json:
                cloudevent_payload = request.json
            elif request.data:
                cloudevent_payload = json.loads(request.data.decode('utf-8'))
            
            if cloudevent_payload:
                 app.logger.warning(f"RID-{request_id}: Request was not 'application/cloudevents+json' nor clearly binary CE. Parsed as plain JSON. This might be raw data from broker.")
            else:
                app.logger.error(f"RID-{request_id}: Request body could not be parsed as JSON.")
                return jsonify({"error": "Invalid request body format", "request_id": request_id}), 400

        if not cloudevent_payload:
             app.logger.error(f"RID-{request_id}: Could not obtain event payload from request.")
             return jsonify({"error": "Empty or unparsable event payload", "request_id": request_id}), 400

        app.logger.info(f"RID-{request_id}: Full event payload received by Flask: {json.dumps(cloudevent_payload, indent=2)}")

        # Now, extract S3 info defensively, assuming cloudevent_payload might be
        # either a full CloudEvent, or just the MinIO JSON (the 'data' part)
        minio_event_data = cloudevent_payload.get('data') if isinstance(cloudevent_payload, dict) and 'data' in cloudevent_payload else cloudevent_payload
        
        if not isinstance(minio_event_data, dict):
            app.logger.error(f"RID-{request_id}: Could not extract a dictionary for MinIO event data. Actual data: {str(minio_event_data)[:500]}")
            return jsonify({"error": "Could not determine MinIO event data structure", "request_id": request_id}), 400

        app.logger.info(f"RID-{request_id}: Effective MinIO event data to parse: {json.dumps(minio_event_data, indent=2)}")

        # Extract bucket_name, object_key, eventName from minio_event_data
        bucket_name = "unknown_bucket"
        object_key = "unknown_key"
        eventName = minio_event_data.get('EventName') # Prefer top-level EventName if direct MinIO JSON

        if 'Records' in minio_event_data and len(minio_event_data.get('Records', [])) > 0:
            record = minio_event_data['Records'][0]
            if not eventName: # Fallback to eventName in record
                eventName = record.get('eventName')
            s3_info = record.get('s3', {})
            object_info = s3_info.get('object', {})
            bucket_info = s3_info.get('bucket', {})
            object_key = object_info.get('key', object_key)
            bucket_name = bucket_info.get('name', bucket_name)
        elif 'Key' in minio_event_data: # For simpler structures or if Key is top-level in data
             object_key = minio_event_data.get('Key', object_key)
             # MinIO webhook might not have top-level Bucket if Records is present
             if 'Bucket' in minio_event_data : bucket_name = minio_event_data.get('Bucket')

        if not eventName: eventName = "s3:ObjectCreated:Put" # Final fallback for event type

        app.logger.info(f"RID-{request_id}: Parsed S3 details: EventName='{eventName}', Bucket='{bucket_name}', Key='{object_key}'")
        
        is_pdf = False
        if object_key and isinstance(object_key, str) and object_key != "unknown_key":
            is_pdf = object_key.lower().endswith(".pdf")
        app.logger.info(f"RID-{request_id}: Is PDF: {is_pdf}")

        # --- PRODUCTION CHECKS (currently commented out for broader debugging) ---
        # if not (eventName and ("ObjectCreated:Put" in eventName or "ObjectCreated:CompleteMultipartUpload" in eventName)):
        #     app.logger.info(f"RID-{request_id}: EventName '{eventName}' is not a relevant object creation event. Skipping.")
        #     return jsonify({"message": "Not a relevant object creation event, skipped.", "request_id": request_id}), 200
        # if not is_pdf:
        #     app.logger.info(f"RID-{request_id}: Object '{object_key}' is not a PDF. Skipping KFP trigger.")
        #     return jsonify({"message": "Object is not a PDF, pipeline not triggered", "request_id": request_id}), 200
        # --- END PRODUCTION CHECKS ---
        app.logger.info(f"RID-{request_id}: DEBUG MODE: Proceeding to KFP logic for bucket='{bucket_name}', key='{object_key}' (PDF check: {is_pdf})")


        if not KFP_ENDPOINT:
            app.logger.error(f"RID-{request_id}: KFP_ENDPOINT not set. Cannot trigger pipeline.")
            return jsonify({"error": "KFP endpoint not configured on server", "request_id": request_id}), 500
        
        kfp_client = get_kfp_client()
        if not kfp_client:
            app.logger.error(f"RID-{request_id}: KFP client could not be initialized. Cannot trigger KFP pipeline.")
            return jsonify({"error": "KFP client initialization failed", "request_id": request_id}), 500

        app.logger.info(f"RID-{request_id}: Looking for KFP pipeline: '{PIPELINE_NAME}'")
        pipeline_id = kfp_client.get_pipeline_id(PIPELINE_NAME) # Prefer get_pipeline_id for robustness
        if not pipeline_id:
            app.logger.error(f"RID-{request_id}: Pipeline '{PIPELINE_NAME}' not found in KFP.")
            return jsonify({"error": f"Pipeline '{PIPELINE_NAME}' not found", "request_id": request_id}), 404
        app.logger.info(f"RID-{request_id}: Found KFP Pipeline ID: {pipeline_id}")

        # Get or create experiment
        try:
            experiment = kfp_client.get_experiment(experiment_name=KFP_EXPERIMENT_NAME)
            app.logger.info(f"RID-{request_id}: Using existing KFP Experiment ID: {experiment.id} (Name: {KFP_EXPERIMENT_NAME})")
        except Exception as exp_e: # Catching a broader exception as KFP SDK might raise various types if not found
            app.logger.warning(f"RID-{request_id}: Experiment '{KFP_EXPERIMENT_NAME}' not found or error getting it: {exp_e}. Attempting to create it.")
            try:
                experiment = kfp_client.create_experiment(name=KFP_EXPERIMENT_NAME)
                app.logger.info(f"RID-{request_id}: Created KFP Experiment ID: {experiment.id} (Name: {KFP_EXPERIMENT_NAME})")
            except Exception as create_exp_e:
                app.logger.error(f"RID-{request_id}: Failed to create KFP experiment '{KFP_EXPERIMENT_NAME}': {create_exp_e}", exc_info=True)
                return jsonify({"error": f"Failed to create KFP experiment: {create_exp_e}", "request_id": request_id}), 500
        
        # Define pipeline parameters for your KFP pipeline
        # Ensure these match the parameters defined in your KFP pipeline component
        pipeline_params = {
            'pdf_s3_bucket': bucket_name,
            'pdf_s3_key': object_key,
            'processing_timestamp': datetime.now(timezone.utc).isoformat() # Fresh timestamp for the run
        }
        app.logger.info(f"RID-{request_id}: Pipeline parameters for KFP run: {json.dumps(pipeline_params)}")

        run_name = f"PDF Event - {object_key.replace('/', '-')} - {request_id[:8]}"
        run_result = kfp_client.run_pipeline(
            experiment_id=experiment.id,
            job_name=run_name, # job_name is often preferred for UI, run_name is also an alias
            pipeline_id=pipeline_id, # Use pipeline_id for KFP SDK v1
            # pipeline_package_path=PIPELINE_PACKAGE_PATH, # Use if uploading package directly, not for registered pipeline
            params=pipeline_params
        )
        run_url = f"{KFP_ENDPOINT}/#/runs/details/{run_result.id}"
        app.logger.info(f"RID-{request_id}: KFP run successfully triggered: ID={run_result.id}, Name='{run_result.name}', URL: {run_url}")
        
        return jsonify({
            "message": "KFP pipeline successfully triggered",
            "kfp_run_id": run_result.id,
            "kfp_run_url": run_url,
            "processed_bucket": bucket_name,
            "processed_key": object_key,
            "cloud_event_id_received": ce_id, # Log the CE id if available
            "request_id": request_id
        }), 200

    except Exception as e:
        app.logger.error(f"RID-{request_id}: Global exception in handle_s3_event: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}", "request_id": request_id}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't')
    
    app.logger.info(f"=== Starting S3 Event Handler (Local Flask Server / Direct Execution) ===")
    app.logger.info(f"Log level: {LOG_LEVEL}")
    app.logger.info(f"KFP Endpoint: {KFP_ENDPOINT if KFP_ENDPOINT else 'NOT SET'}")
    app.logger.info(f"Pipeline Name: {PIPELINE_NAME}")
    app.logger.info(f"Experiment Name: {KFP_EXPERIMENT_NAME}")
    app.logger.info(f"Flask server running on host 0.0.0.0, port: {port}")
    app.logger.info(f"Flask debug mode: {debug_mode}")

    app.run(host='0.0.0.0', port=port, debug=debug_mode)