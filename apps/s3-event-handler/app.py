import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone # Removed timedelta as it wasn't used
from flask import Flask, request, jsonify, g, has_request_context # Added has_request_context
from kfp import Client as KFPClient
from kfp_server_api import ApiClient, Configuration # For more control over KFP client config
import kfp_server_api # For KFP API specific exceptions
import urllib3 # To disable SSL warnings if verify_ssl is False

# --- Environment Variables ---
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
# KFP_BEARER_TOKEN = os.environ.get("KFP_BEARER_TOKEN") # Kept for reference, but SA token is preferred
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "Simple PDF Processing Pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
# VERBOSE_REQUEST_LOGGING = os.environ.get("VERBOSE_REQUEST_LOGGING", "true").lower() == "true" # If you want to control this

# --- Logging Setup ---
class RequestFormatter(logging.Formatter):
    def format(self, record):
        if has_request_context() and hasattr(g, 'request_id'):
            record.request_id = g.request_id
        else:
            record.request_id = 'NO_FLASK_CONTEXT' # Placeholder for logs outside request context
        return super().format(record)

root_logger = logging.getLogger()
try:
    effective_log_level = getattr(logging, LOG_LEVEL)
except AttributeError:
    print(f"Warning: Invalid LOG_LEVEL '{LOG_LEVEL}'. Defaulting to DEBUG.", file=sys.stderr)
    effective_log_level = logging.DEBUG
root_logger.setLevel(effective_log_level)

if root_logger.handlers:
    root_logger.handlers.clear() # Clear existing handlers to avoid duplicates if re-run
handler = logging.StreamHandler(sys.stdout) # Log to stdout
handler.setLevel(effective_log_level)
# Updated formatter string to match your latest logs for consistency
formatter = RequestFormatter('%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s - %(module)s:%(lineno)d')
handler.setFormatter(formatter)
root_logger.addHandler(handler)

app = Flask(__name__)
# Ensure Flask's logger uses the same handlers and level
app.logger.handlers = root_logger.handlers
app.logger.setLevel(effective_log_level)

# Configure Gunicorn's loggers to use the same setup if possible,
# or at least prevent them from causing errors with the custom formatter.
gunicorn_error_logger = logging.getLogger('gunicorn.error')
gunicorn_error_logger.handlers = root_logger.handlers
gunicorn_error_logger.setLevel(effective_log_level)
gunicorn_access_logger = logging.getLogger('gunicorn.access')
# For Gunicorn access logs, a simpler formatter might be better if RequestFormatter still errors
# For now, let's try with the resilient RequestFormatter
gunicorn_access_logger.handlers = root_logger.handlers
gunicorn_access_logger.setLevel(effective_log_level) # Or INFO for less verbose access logs

app.logger.info(f"Flask app initialized. Log level: {LOG_LEVEL}")
app.logger.info(f"KFP_ENDPOINT: {KFP_ENDPOINT}")
app.logger.info(f"KFP_PIPELINE_NAME: {PIPELINE_NAME}")
app.logger.info(f"KFP_EXPERIMENT_NAME: {KFP_EXPERIMENT_NAME}")


@app.before_request
def before_request_logging_extended():
    g.request_id = str(uuid.uuid4())
    # Use app.logger which is configured
    app.logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG): # Check level before expensive operations
        headers_dict = dict(request.headers)
        # Mask Authorization header if present
        if "Authorization" in headers_dict:
            headers_dict["Authorization"] = "***MASKED***"
        app.logger.debug(f"RID-{g.request_id}: Request headers: {json.dumps(headers_dict, indent=2)}")
        if request.data and len(request.data) > 0 : # Check if data exists and is not empty
             try:
                # Log only a sample if it's too large
                raw_body_sample = request.get_data(as_text=True)[:500]
                app.logger.debug(f"RID-{g.request_id}: Raw request body sample (first 500 chars): {raw_body_sample}")
             except Exception:
                app.logger.debug(f"RID-{g.request_id}: Request data present but could not be decoded as text for sample.")
        else:
            app.logger.debug(f"RID-{g.request_id}: No request body or empty body.")


@app.after_request
def after_request_logging_extended(response):
    request_id = getattr(g, 'request_id', 'NO_REQUEST_ID_IN_G_AFTER') # Fallback
    response_data_sample = response.get_data(as_text=True)[:200]
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, Response data sample (first 200 chars): {response_data_sample}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client."""
    request_id = getattr(g, 'request_id', 'KFP_CLIENT_INIT') # Use request_id if in context

    if not KFP_ENDPOINT:
        app.logger.error(f"RID-{request_id}: KFP_ENDPOINT environment variable not set. Cannot initialize KFP client.")
        return None

    app.logger.info(f"RID-{request_id}: Validating KFP endpoint: {KFP_ENDPOINT}")
    if not KFP_ENDPOINT.startswith(('http://', 'https://')):
        app.logger.error(f"RID-{request_id}: Invalid KFP_ENDPOINT format. Must start with http:// or https://. Got: {KFP_ENDPOINT}")
        return None

    app.logger.info(f"RID-{request_id}: Attempting to initialize KFP Client for endpoint: {KFP_ENDPOINT}")
    
    token = None
    if os.path.exists(KFP_SA_TOKEN_PATH):
        try:
            with open(KFP_SA_TOKEN_PATH, 'r') as f:
                token = f.read().strip()
            app.logger.debug(f"RID-{request_id}: Successfully read service account token from {KFP_SA_TOKEN_PATH}")
        except Exception as token_err:
            app.logger.warning(f"RID-{request_id}: Could not read service account token from {KFP_SA_TOKEN_PATH}: {token_err}")
            token = None # Ensure token is None if read fails

    try:
        kfp_api_config = Configuration(host=KFP_ENDPOINT)
        
        # Attempt to use the in-cluster service CA bundle for HTTPS
        # This is the preferred method for self-signed/internal CAs in OpenShift
        service_ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
        if KFP_ENDPOINT.startswith('https://') and os.path.exists(service_ca_path):
            app.logger.info(f"RID-{request_id}: Configuring KFP client to use service CA bundle: {service_ca_path}")
            kfp_api_config.ssl_ca_cert = service_ca_path
            kfp_api_config.verify_ssl = True # Explicitly enable verification with custom CA
        elif KFP_ENDPOINT.startswith('https://'):
            app.logger.warning(f"RID-{request_id}: Service CA bundle not found at {service_ca_path} for HTTPS KFP endpoint. SSL verification might use system CAs or fail if KFP API uses internal certs.")
            # If you must disable SSL verification (NOT FOR PRODUCTION):
            # app.logger.warning("RID-{request_id}: Disabling KFP client SSL verification. DEVELOPMENT/DEBUG ONLY.")
            # kfp_api_config.verify_ssl = False
            # urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        else: # HTTP endpoint
            app.logger.info(f"RID-{request_id}: KFP endpoint is HTTP, SSL verification not applicable.")

        # Configure authentication token
        if token:
            # For KFP SDK v1.x, token is often set in the configuration's api_key
            kfp_api_config.api_key['authorization'] = token
            kfp_api_config.api_key_prefix['authorization'] = 'Bearer'
            app.logger.info(f"RID-{request_id}: Using ServiceAccount token for KFP API authentication.")
        # elif KFP_BEARER_TOKEN: # If you plan to use an explicit bearer token env var
        #     kfp_api_config.api_key['authorization'] = KFP_BEARER_TOKEN
        #     kfp_api_config.api_key_prefix['authorization'] = 'Bearer'
        #     app.logger.info(f"RID-{request_id}: Using KFP_BEARER_TOKEN for KFP API authentication.")
        else:
            app.logger.info(f"RID-{request_id}: No explicit token (SA or KFP_BEARER_TOKEN) provided for KFP API. Client will attempt unauthenticated or rely on ambient credentials if supported.")

        api_client_instance = ApiClient(configuration=kfp_api_config)
        client = KFPClient(api_client=api_client_instance)
        app.logger.info(f"RID-{request_id}: KFP Client object created.")

        # Optional: Verify client by making a simple call
        try:
            app.logger.info(f"RID-{request_id}: Verifying KFP client connection by listing experiments...")
            # Note: list_experiments might require specific RBAC.
            # If this fails due to permissions, the client might still be usable for run_pipeline.
            experiments = client.list_experiments(page_size=1) 
            app.logger.info(f"RID-{request_id}: Successfully listed experiments (found {len(experiments.experiments if experiments and hasattr(experiments, 'experiments') else [])}). KFP API connection verified.")
        except kfp_server_api.ApiException as e_kfp_api:
            app.logger.error(f"RID-{request_id}: KFP API call (list_experiments) failed during client verification: {e_kfp_api.reason} (Status: {e_kfp_api.status}) Body: {e_kfp_api.body}", exc_info=False)
        except Exception as verify_err:
            app.logger.warning(f"RID-{request_id}: KFP client created, but API verification call (list_experiments) failed: {str(verify_err)}.")
            if not isinstance(verify_err, kfp_server_api.ApiException):
                 app.logger.error("RID-{request_id}: Full stack trace for KFP verification error:", exc_info=True)
        
        return client

    except Exception as e:
        app.logger.error(f"RID-{request_id}: Generic exception during KFP client initialization: {str(e)}", exc_info=True)
        return None


@app.route('/healthz', methods=['GET'])
def healthz():
    # request_id will be set by @app.before_request for Flask-handled requests
    request_id = getattr(g, 'request_id', 'HEALTHZ_NO_G')
    app.logger.debug(f"RID-{request_id}: Health check request received at /healthz")
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200
    

@app.route('/', methods=['POST'])
def handle_s3_event():
    request_id = getattr(g, 'request_id', "NO_REQUEST_ID_IN_G_MAIN") # Fallback for request_id
    app.logger.info(f"RID-{request_id}: ==== POST / request received by KFP-S3-TRIGGER user-container ====")
    
    app.logger.info(f"RID-{request_id}: Request Content-Type: {request.content_type}")
    
    # Extract CloudEvent attributes from headers if present (binary mode)
    cloudevent_headers = { k.lower(): v for k, v in request.headers.items() if k.lower().startswith('ce-')}
    if cloudevent_headers:
        app.logger.info(f"RID-{request_id}: Received CloudEvent HTTP headers: {json.dumps(cloudevent_headers, indent=2)}")

    cloudevent_payload = None
    is_binary_cloudevent = bool(cloudevent_headers.get("ce-specversion"))

    try:
        if "application/cloudevents+json" in str(request.content_type).lower():
            cloudevent_payload = request.json
            app.logger.info(f"RID-{request_id}: Parsed structured CloudEvent from JSON body.")
            minio_event_data = cloudevent_payload.get('data') # In structured mode, S3 event is in 'data'
        elif is_binary_cloudevent and request.data:
            app.logger.info(f"RID-{request_id}: Detected binary CloudEvent mode based on ce-* headers and request data.")
            # In binary mode, the request body *is* the event data
            minio_event_data = json.loads(request.data.decode('utf-8'))
            app.logger.info(f"RID-{request_id}: Parsed request.data as JSON for binary CloudEvent data.")
        elif request.is_json: # Fallback: if Content-Type is application/json but not cloudevents+json
            minio_event_data = request.json
            app.logger.warning(f"RID-{request_id}: Request Content-Type was 'application/json' (not structured CE). Assuming body is MinIO event data.")
        elif request.data: # Fallback: try to parse non-JSON data as JSON
             minio_event_data = json.loads(request.data.decode('utf-8'))
             app.logger.warning(f"RID-{request_id}: Request data present but Content-Type not JSON. Attempted to parse as JSON. This might be raw data from broker.")
        else:
            app.logger.error(f"RID-{request_id}: Request body is empty or not decodable/parsable as JSON.")
            return jsonify({"error": "Request body is empty or not JSON", "request_id": request_id}), 400

        if not minio_event_data or not isinstance(minio_event_data, dict):
            app.logger.error(f"RID-{request_id}: Could not extract a dictionary for MinIO event data. Actual data: {str(minio_event_data)[:500]}")
            return jsonify({"error": "Could not determine MinIO event data structure", "request_id": request_id}), 400
        
        app.logger.info(f"RID-{request_id}: Effective MinIO event data to parse: {json.dumps(minio_event_data, indent=2)}")

        # Extract S3 info
        bucket_name = "unknown_bucket"
        object_key = "unknown_key"
        # MinIO webhooks often put event name at top level, or inside Records[0]
        eventName = minio_event_data.get('EventName') 

        if 'Records' in minio_event_data and isinstance(minio_event_data.get('Records'), list) and len(minio_event_data['Records']) > 0:
            record = minio_event_data['Records'][0]
            if not eventName and 'eventName' in record: # Prefer top-level, fallback to record
                eventName = record.get('eventName')
            
            s3_info = record.get('s3', {})
            if isinstance(s3_info, dict):
                object_info = s3_info.get('object', {})
                bucket_info = s3_info.get('bucket', {})
                if isinstance(object_info, dict):
                    object_key = object_info.get('key', object_key)
                if isinstance(bucket_info, dict):
                    bucket_name = bucket_info.get('name', bucket_name)
        elif 'Key' in minio_event_data: # Handle simpler MinIO event structures if Key is top-level
             object_key = minio_event_data.get('Key', object_key)
             if 'Bucket' in minio_event_data and 'name' in minio_event_data['Bucket']:
                 bucket_name = minio_event_data['Bucket']['name']

        if not eventName: eventName = "s3:ObjectCreated:Put" # Default if not found

        app.logger.info(f"RID-{request_id}: Parsed S3 details: EventName='{eventName}', Bucket='{bucket_name}', Key='{object_key}'")
        
        is_pdf = False
        if object_key and isinstance(object_key, str) and object_key != "unknown_key":
            is_pdf = object_key.lower().endswith(".pdf")
        app.logger.info(f"RID-{request_id}: Is PDF: {is_pdf}")

        # For debugging, let's proceed even if not a PDF or not a PUT event
        app.logger.info(f"RID-{request_id}: DEBUG MODE: Proceeding to KFP logic for bucket='{bucket_name}', key='{object_key}' (PDF check: {is_pdf})")

        # if not (eventName and ("ObjectCreated:Put" in eventName or "ObjectCreated:CompleteMultipartUpload" in eventName)):
        #     app.logger.info(f"RID-{request_id}: EventName '{eventName}' is not a relevant object creation event. Skipping.")
        #     return jsonify({"message": "Not a relevant object creation event, skipped.", "request_id": request_id}), 200
        # if not is_pdf:
        #     app.logger.info(f"RID-{request_id}: Object '{object_key}' is not a PDF. Skipping KFP trigger.")
        #     return jsonify({"message": "Object is not a PDF, pipeline not triggered", "request_id": request_id}), 200

        if not KFP_ENDPOINT:
            app.logger.error(f"RID-{request_id}: KFP_ENDPOINT not set. Cannot trigger pipeline.")
            return jsonify({"error": "KFP endpoint not configured on server", "request_id": request_id}), 500
        
        # Call the KFP client initialization function
        kfp_client = get_kfp_client() # This was the source of the NameError
        if not kfp_client:
            app.logger.error(f"RID-{request_id}: KFP client could not be initialized. Cannot trigger KFP pipeline.")
            # The error from get_kfp_client would have already been logged.
            return jsonify({"error": "KFP client initialization failed", "request_id": request_id}), 500

        app.logger.info(f"RID-{request_id}: Looking for KFP pipeline: '{PIPELINE_NAME}'")
        pipeline_id = None
        try:
            pipeline_id = kfp_client.get_pipeline_id(PIPELINE_NAME)
        except Exception as e_get_id:
            app.logger.error(f"RID-{request_id}: Error getting KFP Pipeline ID for '{PIPELINE_NAME}': {str(e_get_id)}", exc_info=True)
            # Fall through, pipeline_id will be None
            
        if not pipeline_id:
            app.logger.error(f"RID-{request_id}: Pipeline '{PIPELINE_NAME}' not found in KFP or error retrieving ID.")
            return jsonify({"error": f"Pipeline '{PIPELINE_NAME}' not found or error retrieving ID", "request_id": request_id}), 404
        app.logger.info(f"RID-{request_id}: Found KFP Pipeline ID: {pipeline_id}")

        experiment = None
        try:
            experiment = kfp_client.get_experiment(experiment_name=KFP_EXPERIMENT_NAME)
            app.logger.info(f"RID-{request_id}: Using existing KFP Experiment ID: {experiment.id} (Name: {KFP_EXPERIMENT_NAME})")
        except Exception as exp_e: 
            app.logger.warning(f"RID-{request_id}: Experiment '{KFP_EXPERIMENT_NAME}' not found or error getting it: {exp_e}. Attempting to create it.")
            try:
                experiment = kfp_client.create_experiment(name=KFP_EXPERIMENT_NAME)
                app.logger.info(f"RID-{request_id}: Created KFP Experiment ID: {experiment.id} (Name: {KFP_EXPERIMENT_NAME})")
            except Exception as create_exp_e:
                app.logger.error(f"RID-{request_id}: Failed to create KFP experiment '{KFP_EXPERIMENT_NAME}': {create_exp_e}", exc_info=True)
                return jsonify({"error": f"Failed to create KFP experiment: {create_exp_e}", "request_id": request_id}), 500
        
        pipeline_params = {
            'pdf_s3_bucket': bucket_name,
            'pdf_s3_key': object_key,
            'processing_timestamp': datetime.now(timezone.utc).isoformat()
        }
        app.logger.info(f"RID-{request_id}: Pipeline parameters for KFP run: {json.dumps(pipeline_params)}")

        run_name = f"PDF Event - {object_key.replace('/', '-')} - {request_id[:8]}"
        app.logger.info(f"RID-{request_id}: Submitting KFP run with name: {run_name}")
        
        run_result = kfp_client.run_pipeline(
            experiment_id=experiment.id,
            job_name=run_name, 
            pipeline_id=pipeline_id,
            params=pipeline_params
        )
        run_url = f"{KFP_ENDPOINT}/#/runs/details/{run_result.id}" if hasattr(run_result, 'id') else "N/A"
        run_id_str = run_result.id if hasattr(run_result, 'id') else "N/A"
        run_name_str = run_result.name if hasattr(run_result, 'name') else run_name # KFP SDK might return a name in run_result
        
        app.logger.info(f"RID-{request_id}: KFP run successfully triggered: ID={run_id_str}, Name='{run_name_str}', URL: {run_url}")
        
        # Extract the CloudEvent ID from headers if available (for binary mode)
        # or from the payload if structured (though your current parsing assumes binary for data extraction)
        cloud_event_id_received = cloudevent_headers.get("ce-id")
        if not cloud_event_id_received and isinstance(cloudevent_payload, dict):
            cloud_event_id_received = cloudevent_payload.get("id")


        return jsonify({
            "message": "KFP pipeline successfully triggered",
            "kfp_run_id": run_id_str,
            "kfp_run_url": run_url,
            "processed_bucket": bucket_name,
            "processed_key": object_key,
            "cloud_event_id_received": cloud_event_id_received if cloud_event_id_received else "N/A",
            "request_id": request_id
        }), 200

    except json.JSONDecodeError as e_json:
        app.logger.error(f"RID-{request_id}: JSONDecodeError processing webhook: {str(e_json)}. Raw data (first 500B): {raw_body_sample}", exc_info=True)
        return jsonify({"status": "error", "message": f"Invalid JSON payload: {str(e_json)}", "request_id": request_id}), 400
    except Exception as e: # Catch-all for other unexpected errors
        app.logger.error(f"RID-{request_id}: Global exception in handle_s3_event: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}", "request_id": request_id}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    # Gunicorn typically handles debug mode, but this is for direct Flask run
    flask_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't') 
    
    # This block only runs if script is executed directly (python app.py), not via Gunicorn
    app.logger.info(f"=== Starting S3 Event Handler (Local Flask Server / Direct Execution) ===")
    app.logger.info(f"Log level: {LOG_LEVEL}")
    app.logger.info(f"KFP Endpoint: {KFP_ENDPOINT if KFP_ENDPOINT else 'NOT SET'}")
    app.logger.info(f"Pipeline Name: {PIPELINE_NAME}")
    app.logger.info(f"Experiment Name: {KFP_EXPERIMENT_NAME}")
    app.logger.info(f"Flask server running on host 0.0.0.0, port: {port}")
    app.logger.info(f"Flask debug mode: {flask_debug_mode}")

    app.run(host='0.0.0.0', port=port, debug=flask_debug_mode)