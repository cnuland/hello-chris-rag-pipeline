import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, has_request_context # Added has_request_context
from kfp import Client as KFPClient
from kfp_server_api.configuration import Configuration as KFPConfiguration # Alias for clarity
from kfp_server_api.api_client import ApiClient as KFPApiClient # Alias for clarity
import kfp_server_api # For KFP API specific exceptions
import urllib3 # To disable SSL warnings if verify_ssl is set to False

# --- Environment Variables ---
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "Simple PDF Processing Pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# --- Logging Setup ---
class RequestFormatter(logging.Formatter):
    def format(self, record):
        if has_request_context() and hasattr(g, 'request_id'):
            record.request_id = g.request_id
        else:
            record.request_id = 'NO_FLASK_CONTEXT'
        return super().format(record)

root_logger = logging.getLogger()
try:
    effective_log_level = getattr(logging, LOG_LEVEL)
except AttributeError:
    print(f"Warning: Invalid LOG_LEVEL '{LOG_LEVEL}'. Defaulting to DEBUG.", file=sys.stderr)
    effective_log_level = logging.DEBUG
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

app.logger.info(f"Flask app initialized. Log level: {LOG_LEVEL}")
app.logger.info(f"KFP_ENDPOINT: {KFP_ENDPOINT}")
app.logger.info(f"KFP_PIPELINE_NAME: {PIPELINE_NAME}")
app.logger.info(f"KFP_EXPERIMENT_NAME: {KFP_EXPERIMENT_NAME}")


@app.before_request
def before_request_logging_extended():
    g.request_id = str(uuid.uuid4())
    app.logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG): 
        headers_dict = dict(request.headers)
        if "Authorization" in headers_dict:
            headers_dict["Authorization"] = "***MASKED***"
        app.logger.debug(f"RID-{g.request_id}: Request headers: {json.dumps(headers_dict, indent=2)}")
        if request.data and len(request.data) > 0 : 
             try:
                raw_body_sample = request.get_data(as_text=True)[:500]
                app.logger.debug(f"RID-{g.request_id}: Raw request body sample (first 500 chars): {raw_body_sample}")
             except Exception:
                app.logger.debug(f"RID-{g.request_id}: Request data present but could not be decoded as text for sample.")
        else:
            app.logger.debug(f"RID-{g.request_id}: No request body or empty body.")


@app.after_request
def after_request_logging_extended(response):
    request_id = getattr(g, 'request_id', 'NO_REQUEST_ID_IN_G_AFTER') 
    response_data_sample = response.get_data(as_text=True)[:200]
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, Response data sample (first 200 chars): {response_data_sample}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client."""
    request_id = getattr(g, 'request_id', 'KFP_CLIENT_INIT_NO_CTX') 
    if has_request_context(): # Check again in case this is called outside a request by mistake
        request_id = g.request_id


    if not KFP_ENDPOINT:
        app.logger.error(f"RID-{request_id}: KFP_ENDPOINT environment variable not set.")
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
            app.logger.debug(f"RID-{request_id}: Successfully read SA token from {KFP_SA_TOKEN_PATH}")
        except Exception as token_err:
            app.logger.warning(f"RID-{request_id}: Could not read SA token: {token_err}")
            token = None

    try:
        # Create a KFP Configuration object to customize ApiClient settings
        # For KFP SDK v1.x, modifying the default configuration used by ApiClient can work.
        config = KFPConfiguration.get_default_copy()
        config.host = KFP_ENDPOINT # Set the host for this config object

        service_ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
        if KFP_ENDPOINT.startswith('https://'):
            if os.path.exists(service_ca_path):
                app.logger.info(f"RID-{request_id}: Configuring KFP client with service CA: {service_ca_path}")
                config.ssl_ca_cert = service_ca_path
                config.verify_ssl = True # Ensure verification is enabled when using custom CA
            else:
                app.logger.warning(f"RID-{request_id}: Service CA bundle not found at {service_ca_path} for HTTPS KFP endpoint. Client will use default system CAs. If KFP API uses internal self-signed certs, this might fail verification.")
                # If you still face SSL issues and absolutely must (for dev/debug ONLY):
                # app.logger.warning(f"RID-{request_id}: Fallback: Disabling KFP client SSL verification. NOT FOR PRODUCTION.")
                # config.verify_ssl = False
                # urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        else:
            app.logger.info(f"RID-{request_id}: KFP endpoint is HTTP. SSL verification not directly applicable through config.verify_ssl.")

        if token:
            config.api_key['authorization'] = token
            config.api_key_prefix['authorization'] = 'Bearer'
            app.logger.info(f"RID-{request_id}: KFP client config prepared with SA token.")
        else:
            app.logger.info(f"RID-{request_id}: No SA token provided to KFP client config. Relying on ambient auth if any.")

        # For KFP SDK v1.x, KFPClient() usually takes `host` and `existing_token` (or other auth params)
        # and creates its own ApiClient internally, which should pick up the default Configuration.
        # Let's explicitly set the default configuration for kfp_server_api.
        KFPConfiguration.set_default(config)
        
        # Now initialize KFPClient. It should use the default configuration we just set.
        if token:
            # The 'existing_token' argument expects the token string directly, not "Bearer <token>"
            # The SDK's ApiClient typically adds the "Bearer" prefix if it sees config.api_key_prefix['authorization'] = 'Bearer'
            client = KFPClient(host=KFP_ENDPOINT, existing_token=token)
            app.logger.info(f"RID-{request_id}: KFPClient initialized with host and existing_token.")
        else:
            client = KFPClient(host=KFP_ENDPOINT)
            app.logger.info(f"RID-{request_id}: KFPClient initialized with host only (no explicit token).")
        
        app.logger.info(f"RID-{request_id}: KFP Client object created.")

        # Test with a simple call
        try:
            app.logger.info(f"RID-{request_id}: Verifying KFP client connection by listing experiments...")
            experiments = client.list_experiments(page_size=1)
            experiment_count = len(experiments.experiments) if experiments and hasattr(experiments, 'experiments') and experiments.experiments is not None else 0
            app.logger.info(f"RID-{request_id}: Successfully listed experiments (found {experiment_count}). KFP API connection verified.")
        except kfp_server_api.ApiException as e_kfp_api:
            app.logger.error(f"RID-{request_id}: KFP API call (list_experiments) failed: Status {e_kfp_api.status}, Reason: {e_kfp_api.reason}, Body: {e_kfp_api.body}", exc_info=False)
        except Exception as verify_err:
            app.logger.warning(f"RID-{request_id}: KFP client created, but API verification call (list_experiments) failed: {str(verify_err)}.")
            if not isinstance(verify_err, kfp_server_api.ApiException):
                 app.logger.error(f"RID-{request_id}: Full stack trace for KFP verification error:", exc_info=True)
        
        return client

    except Exception as e:
        app.logger.error(f"RID-{request_id}: Exception during KFP client initialization: {str(e)}", exc_info=True)
        return None

@app.route('/healthz', methods=['GET'])
def healthz():
    request_id = getattr(g, 'request_id', 'HEALTHZ_NO_G_ROUTE') 
    app.logger.debug(f"RID-{request_id}: Health check request received at /healthz")
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200
    

@app.route('/', methods=['POST'])
def handle_s3_event():
    request_id = getattr(g, 'request_id', "S3_EVENT_NO_G_ROUTE") 
    app.logger.info(f"RID-{request_id}: ==== POST / request received by KFP-S3-TRIGGER user-container ====")
    
    app.logger.info(f"RID-{request_id}: Request Content-Type: {request.content_type}")
    
    cloudevent_headers = { k.lower(): v for k, v in request.headers.items() if k.lower().startswith('ce-')}
    if cloudevent_headers:
        app.logger.info(f"RID-{request_id}: Received CloudEvent HTTP headers: {json.dumps(cloudevent_headers, indent=2)}")

    minio_event_data = None # Will hold the S3 event details
    cloud_event_id_received = "N/A"

    try:
        # Try to determine if it's a structured or binary CloudEvent
        if "application/cloudevents+json" in str(request.content_type).lower():
            full_payload = request.json
            app.logger.info(f"RID-{request_id}: Parsed as structured CloudEvent (application/cloudevents+json).")
            minio_event_data = full_payload.get('data') if isinstance(full_payload, dict) else None
            cloud_event_id_received = full_payload.get('id', "N/A") if isinstance(full_payload, dict) else "N/A"
        elif cloudevent_headers.get("ce-specversion") and request.data:
            app.logger.info(f"RID-{request_id}: Detected binary CloudEvent mode based on ce-* headers and request data.")
            minio_event_data = json.loads(request.data.decode('utf-8'))
            app.logger.info(f"RID-{request_id}: Parsed request.data as JSON for binary CloudEvent data.")
            cloud_event_id_received = cloudevent_headers.get("ce-id", "N/A")
        elif request.is_json: # Fallback for plain application/json
            minio_event_data = request.json
            app.logger.warning(f"RID-{request_id}: Request Content-Type was 'application/json' (not a CloudEvent). Assuming body is MinIO event data.")
        elif request.data : # Last resort: try to parse data as JSON if any data present
            try:
                minio_event_data = json.loads(request.data.decode('utf-8'))
                app.logger.warning(f"RID-{request_id}: Request data present, Content-Type not JSON. Attempted to parse as JSON.")
            except json.JSONDecodeError:
                app.logger.error(f"RID-{request_id}: Request data present but could not be parsed as JSON.")
                raw_body_sample = request.get_data(as_text=True)[:500]
                app.logger.debug(f"RID-{request_id}: Raw request body sample (first 500 chars for non-JSON): {raw_body_sample}")
                return jsonify({"error": "Request body not decodable/parsable as JSON", "request_id": request_id}), 400
        else:
            app.logger.error(f"RID-{request_id}: Request body is empty or Content-Type not supported for direct parsing.")
            return jsonify({"error": "Empty request body or unsupported Content-Type for direct parsing", "request_id": request_id}), 400

        if not minio_event_data or not isinstance(minio_event_data, dict):
            app.logger.error(f"RID-{request_id}: Could not extract a dictionary for MinIO event data after parsing. Data: {str(minio_event_data)[:500]}")
            return jsonify({"error": "Could not determine MinIO event data structure from payload", "request_id": request_id}), 400
        
        app.logger.info(f"RID-{request_id}: Effective MinIO event data to parse: {json.dumps(minio_event_data, indent=2)}")

        bucket_name = "unknown_bucket"
        object_key = "unknown_key"
        eventName = minio_event_data.get('EventName')

        if 'Records' in minio_event_data and isinstance(minio_event_data.get('Records'), list) and len(minio_event_data['Records']) > 0:
            record = minio_event_data['Records'][0]
            if not eventName and 'eventName' in record:
                eventName = record.get('eventName')
            s3_info = record.get('s3', {})
            if isinstance(s3_info, dict):
                object_info = s3_info.get('object', {})
                bucket_info = s3_info.get('bucket', {})
                if isinstance(object_info, dict): object_key = object_info.get('key', object_key)
                if isinstance(bucket_info, dict): bucket_name = bucket_info.get('name', bucket_name)
        elif 'Key' in minio_event_data:
             object_key = minio_event_data.get('Key', object_key)
             if 'Bucket' in minio_event_data and isinstance(minio_event_data.get('Bucket'), dict) and 'name' in minio_event_data['Bucket']:
                 bucket_name = minio_event_data['Bucket']['name']
             elif 'bucket' in minio_event_data and isinstance(minio_event_data.get('bucket'), dict) and 'name' in minio_event_data['bucket']: # some webhook structures
                 bucket_name = minio_event_data['bucket']['name']


        if not eventName: eventName = "s3:ObjectCreated:Put"

        app.logger.info(f"RID-{request_id}: Parsed S3 details: EventName='{eventName}', Bucket='{bucket_name}', Key='{object_key}'")
        
        is_pdf = False
        if object_key and isinstance(object_key, str) and object_key != "unknown_key":
            is_pdf = object_key.lower().endswith(".pdf")
        app.logger.info(f"RID-{request_id}: Is PDF: {is_pdf}")

        app.logger.info(f"RID-{request_id}: DEBUG MODE: Proceeding to KFP logic for bucket='{bucket_name}', key='{object_key}' (PDF check: {is_pdf})")

        if not KFP_ENDPOINT:
            app.logger.error(f"RID-{request_id}: KFP_ENDPOINT not set. Cannot trigger pipeline.")
            return jsonify({"error": "KFP endpoint not configured on server", "request_id": request_id}), 500
        
        kfp_client = get_kfp_client() # This call needs get_kfp_client to be in scope
        if not kfp_client:
            app.logger.error(f"RID-{request_id}: KFP client could not be initialized. Cannot trigger KFP pipeline.")
            return jsonify({"error": "KFP client initialization failed", "request_id": request_id}), 500

        app.logger.info(f"RID-{request_id}: Looking for KFP pipeline: '{PIPELINE_NAME}'")