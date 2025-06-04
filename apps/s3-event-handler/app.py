import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, has_request_context

# KFP SDK v2.x imports (for kfp==2.7.0 compatibility)
from kfp import Client as KFPClient
# kfp_server_api.configuration and api_client are not explicitly used for client setup in this simplified version
import kfp_server_api 
import urllib3 # For disabling InsecureRequestWarning

# --- Environment Variables ---
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token" # Default path for SA token

PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "simple-pdf-processing-pipeline") # Kept for context, not used in this test
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs") # Kept for context, not used in this test
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
KFP_VERIFY_SSL = os.environ.get("KFP_VERIFY_SSL", "true").lower() == "true"
REQUESTS_CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE", "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")


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
app.logger.info(f"KFP_ENDPOINT: {KFP_ENDPOINT if KFP_ENDPOINT else 'NOT SET'}")
app.logger.info(f"KFP_PIPELINE_NAME: {PIPELINE_NAME} (not directly used in this simplified test version)")
app.logger.info(f"KFP_EXPERIMENT_NAME: {KFP_EXPERIMENT_NAME} (not directly used in this simplified test version)")
app.logger.info(f"KFP_VERIFY_SSL: {KFP_VERIFY_SSL}")
app.logger.info(f"REQUESTS_CA_BUNDLE path: {REQUESTS_CA_BUNDLE}")
app.logger.info(f"KFP_SA_TOKEN_PATH: {KFP_SA_TOKEN_PATH}. Will attempt to use this token.")


@app.before_request
def before_request_logging_extended():
    g.request_id = str(uuid.uuid4()) 
    app.logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG): 
        headers_dict = dict(request.headers)
        if "Authorization" in headers_dict: 
            headers_dict["Authorization"] = "***MASKED***"
        app.logger.debug(f"RID-{g.request_id}: Request headers: {json.dumps(headers_dict, indent=2)}")
        
        body_sample = "N/A"
        if request.data:
            try:
                body_sample = request.get_data(as_text=True)[:500] + ('...' if len(request.data) > 500 else '')
            except Exception:
                body_sample = "[Could not decode body as text for sample]"
            app.logger.debug(f"RID-{g.request_id}: Raw request body sample: {body_sample}")
        else:
            app.logger.debug(f"RID-{g.request_id}: No request body or empty body.")

@app.after_request
def after_request_logging_extended(response):
    request_id = getattr(g, 'request_id', 'NO_REQUEST_ID_IN_G_AFTER') 
    response_data_sample = response.get_data(as_text=True)[:200] + ('...' if len(response.get_data(as_text=True)) > 200 else '')
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, Response data sample: {response_data_sample}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client using SA token with existing_token and direct ssl_ca_cert."""
    request_id_str = "KFP_CLIENT_INIT_NO_CTX" 
    if has_request_context() and hasattr(g, 'request_id'):
        request_id_str = g.request_id
    
    current_app_logger = app.logger 

    if not KFP_ENDPOINT:
        current_app_logger.error(f"RID-{request_id_str}: KFP_ENDPOINT environment variable not set.")
        return None

    current_app_logger.info(f"RID-{request_id_str}: Initializing KFP Client for endpoint: {KFP_ENDPOINT}")
    
    sa_token = None
    if os.path.exists(KFP_SA_TOKEN_PATH):
        try:
            with open(KFP_SA_TOKEN_PATH, 'r') as f:
                sa_token = f.read().strip()
            current_app_logger.info(f"RID-{request_id_str}: Successfully read SA token from {KFP_SA_TOKEN_PATH}.")
        except Exception as token_err:
            current_app_logger.error(f"RID-{request_id_str}: Could not read SA token from {KFP_SA_TOKEN_PATH}: {token_err}. KFP calls requiring auth may fail.", exc_info=True)
    else:
        current_app_logger.warning(f"RID-{request_id_str}: SA token path {KFP_SA_TOKEN_PATH} not found. KFP calls requiring auth may fail.")

    ssl_ca_cert_to_use = None
    if KFP_ENDPOINT.startswith('https://'):
        if KFP_VERIFY_SSL:
            if os.path.exists(REQUESTS_CA_BUNDLE):
                ssl_ca_cert_to_use = REQUESTS_CA_BUNDLE
                current_app_logger.info(f"RID-{request_id_str}: KFP SSL verification ENABLED. Will pass ssl_ca_cert: {ssl_ca_cert_to_use}")
            else:
                current_app_logger.warning(f"RID-{request_id_str}: KFP SSL verification ENABLED, but CA bundle '{REQUESTS_CA_BUNDLE}' not found. Will pass ssl_ca_cert=None (relying on system CAs or KFPClient default).")
        else: 
            current_app_logger.warning(f"RID-{request_id_str}: KFP_VERIFY_SSL is false. Will pass ssl_ca_cert=None and disable urllib3 warnings. This effectively disables SSL verification.")
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            ssl_ca_cert_to_use = None # Explicitly set to None if KFP_VERIFY_SSL is false
    else:
        current_app_logger.info(f"RID-{request_id_str}: KFP_ENDPOINT is HTTP. SSL verification not applicable.")

    client = None
    try:
        client_init_kwargs = {
            'host': KFP_ENDPOINT,
            'existing_token': sa_token # Use the SA token read from file (or None if not found/readable)
        }
        if KFP_ENDPOINT.startswith('https://'):
            client_init_kwargs['ssl_ca_cert'] = ssl_ca_cert_to_use
        
        current_app_logger.info(f"RID-{request_id_str}: Initializing KFPClient with: host='{KFP_ENDPOINT}', existing_token (from SA token file), ssl_ca_cert='{ssl_ca_cert_to_use}'")
        client = KFPClient(**client_init_kwargs)
        
        current_app_logger.info(f"RID-{request_id_str}: KFP Client object created for host: {KFP_ENDPOINT}.")
        
        # Verification call - this is part of the client initialization in KFP SDK 2.7.0
        # If it fails here, the client object might not be fully functional or might be None.
        # The try/except below for list_experiments in handle_s3_event will be the actual test.
        
        return client

    except Exception as e:
        current_app_logger.error(f"RID-{request_id_str}: Exception during KFP client initialization: {str(e)}", exc_info=True)
        return None

@app.route('/healthz', methods=['GET'])
def healthz():
    request_id = getattr(g, 'request_id', 'HEALTHZ_NO_G_ROUTE') 
    app.logger.debug(f"RID-{request_id}: Health check request received at /healthz")
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200
    

@app.route('/', methods=['POST'])
def handle_s3_event():
    request_id = getattr(g, 'request_id', "S3_EVENT_NO_G_ROUTE") 
    app.logger.info(f"RID-{request_id}: ==== SIMPLIFIED TEST: POST / request received by KFP-S3-TRIGGER user-container ====")
    
    app.logger.info(f"RID-{request_id}: Request Content-Type: {request.content_type}")
    
    # Basic event logging
    cloudevent_headers = { k.lower(): v for k, v in request.headers.items() if k.lower().startswith('ce-')}
    if cloudevent_headers:
        app.logger.info(f"RID-{request_id}: Received CloudEvent HTTP headers: {json.dumps(cloudevent_headers, indent=2)}")
    
    if request.data:
        try:
            body_sample = request.get_data(as_text=True)[:200] + ('...' if len(request.data) > 200 else '')
            app.logger.info(f"RID-{request_id}: Received request body sample: {body_sample}")
        except Exception:
            app.logger.info(f"RID-{request_id}: Received request body (could not decode as text for sample).")


    kfp_client = get_kfp_client()
    if not kfp_client:
        app.logger.error(f"RID-{request_id}: KFP client could not be initialized. Cannot proceed with test.")
        return jsonify({"status": "error", "message": "KFP client initialization failed", "request_id": request_id}), 500

    try:
        app.logger.info(f"RID-{request_id}: Attempting to start pipeline")
        
        pipelines = kfp_client.list_pipelines()
        pipeline_id = None
        for p in pipelines.pipelines:
            app.logger.info(p)
            if p.name == "simple":
                pipeline_id = p.id
                break

        if not pipeline_id:
            raise ValueError("Pipeline named 'simple' not found")
        experiment = kfp_client.create_experiment(name="test")
        # Start the pipeline
        run = kfp_client.run_pipeline(
            experiment_id=experiment.id,
            job_name=f"simple-run-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            pipeline_id=pipeline_id,
                params={}  # Replace with {"param_name": "value"} if your pipeline requires input parameters
            )
        
        if run is not None:
            app.logger.info(f"RID-{request_id}: Sent request to start pipeline")

            return jsonify({ 
                "message": "Successfully started KFP pipeline.",
                "request_id": request_id
            }), 200
        else:
            app.logger.warning(f"RID-{request_id}: list_experiments returned a response, but no experiments found or experiments attribute is None.")
            return jsonify({"message": "Listed experiments, but no experiments found or response malformed.", "request_id": request_id}), 200

    except kfp_server_api.ApiException as e_kfp_api: 
        app.logger.error(f"RID-{request_id}: KFP API call (list_experiments) failed: Status {e_kfp_api.status}, Reason: {e_kfp_api.reason}", exc_info=False)
        app.logger.debug(f"RID-{request_id}: KFP API Exception Body: {e_kfp_api.body}")
        return jsonify({"status": "error", "message": f"KFP API error: {e_kfp_api.reason}", "details": str(e_kfp_api.body), "request_id": request_id}), 500
    except Exception as e: 
        app.logger.error(f"RID-{request_id}: Global exception during list_experiments test: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error during KFP test: {str(e)}", "request_id": request_id}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    flask_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't') 
    
    app.logger.info(f"=== S3 Event Handler Starting (Direct Flask Run - SIMPLIFIED TEST VERSION) ===")
    app.run(host='0.0.0.0', port=port, debug=flask_debug_mode)