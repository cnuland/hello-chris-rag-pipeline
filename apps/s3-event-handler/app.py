import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, has_request_context
from kfp import Client as KFPClient
from kfp_server_api.configuration import Configuration as KFPConfiguration
import kfp_server_api 
import urllib3

# --- Environment Variables ---
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
# KFP_SA_TOKEN_PATH is the default path for the pod's service account token
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
# New environment variable for a manually provided bearer token
KFP_MANUAL_BEARER_TOKEN = os.environ.get("KFP_BEARER_TOKEN") 

PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "simple-pdf-processing-pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
# Environment variable to control SSL verification
KFP_VERIFY_SSL = os.environ.get("KFP_VERIFY_SSL", "true").lower() == "true"


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
app.logger.info(f"KFP_PIPELINE_NAME: {PIPELINE_NAME}")
app.logger.info(f"KFP_EXPERIMENT_NAME: {KFP_EXPERIMENT_NAME}")
app.logger.info(f"KFP_VERIFY_SSL: {KFP_VERIFY_SSL}")
if KFP_MANUAL_BEARER_TOKEN:
    app.logger.info("KFP_BEARER_TOKEN is SET (token value masked in logs).")
else:
    app.logger.info("KFP_BEARER_TOKEN is NOT SET, will attempt to use SA token.")


@app.before_request
def before_request_logging_extended():
    """Log detailed request information before processing."""
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
    """Log response information after processing."""
    request_id = getattr(g, 'request_id', 'NO_REQUEST_ID_IN_G_AFTER') 
    response_data_sample = response.get_data(as_text=True)[:200] + ('...' if len(response.get_data(as_text=True)) > 200 else '')
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, Response data sample: {response_data_sample}")
    return response

def get_kfp_client():
    """Initializes and returns a KFP client, compatible with KFP SDK v2.x."""
    request_id_str = "KFP_CLIENT_INIT_NO_CTX" 
    if has_request_context() and hasattr(g, 'request_id'):
        request_id_str = g.request_id
    
    current_app_logger = app.logger 

    if not KFP_ENDPOINT:
        current_app_logger.error(f"RID-{request_id_str}: KFP_ENDPOINT environment variable not set.")
        return None

    current_app_logger.info(f"RID-{request_id_str}: Validating KFP endpoint: {KFP_ENDPOINT}")
    if not KFP_ENDPOINT.startswith(('http://', 'https://')):
        current_app_logger.error(f"RID-{request_id_str}: Invalid KFP_ENDPOINT format. Must start with http:// or https://. Got: {KFP_ENDPOINT}")
        return None

    current_app_logger.info(f"RID-{request_id_str}: Attempting to initialize KFP Client (SDK v2 style) for endpoint: {KFP_ENDPOINT}")
    
    token_to_use = None
    if KFP_MANUAL_BEARER_TOKEN:
        token_to_use = KFP_MANUAL_BEARER_TOKEN
        current_app_logger.info(f"RID-{request_id_str}: Using manually provided KFP_BEARER_TOKEN.")
    elif os.path.exists(KFP_SA_TOKEN_PATH):
        try:
            with open(KFP_SA_TOKEN_PATH, 'r') as f:
                token_to_use = f.read().strip()
            current_app_logger.debug(f"RID-{request_id_str}: Successfully read SA token from {KFP_SA_TOKEN_PATH}")
        except Exception as token_err:
            current_app_logger.warning(f"RID-{request_id_str}: Could not read SA token from {KFP_SA_TOKEN_PATH}: {token_err}")
            token_to_use = None 
    else:
        current_app_logger.info(f"RID-{request_id_str}: SA token path {KFP_SA_TOKEN_PATH} not found and KFP_BEARER_TOKEN not set. KFP client will attempt unauthenticated or rely on ambient credentials if any.")

    try:
        default_kfp_config = KFPConfiguration.get_default_copy()
        default_kfp_config.host = KFP_ENDPOINT 

        if KFP_ENDPOINT.startswith('https://'):
            if KFP_VERIFY_SSL:
                service_ca_path = os.environ.get("REQUESTS_CA_BUNDLE", "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")
                if os.path.exists(service_ca_path):
                    current_app_logger.info(f"RID-{request_id_str}: KFP SSL verification ENABLED. Using CA bundle: {service_ca_path}")
                    default_kfp_config.ssl_ca_cert = service_ca_path
                    default_kfp_config.verify_ssl = True
                else:
                    current_app_logger.warning(f"RID-{request_id_str}: KFP SSL verification ENABLED, but CA bundle not found at {service_ca_path}. SSL errors may occur if the KFP API cert is not publicly trusted.")
                    default_kfp_config.verify_ssl = True 
            else: 
                current_app_logger.warning(f"RID-{request_id_str}: KFP_VERIFY_SSL is false. Disabling SSL certificate verification for KFP client. NOT FOR PRODUCTION.")
                default_kfp_config.verify_ssl = False
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        if token_to_use:
            default_kfp_config.api_key['authorization'] = token_to_use
            default_kfp_config.api_key_prefix['authorization'] = 'Bearer'
            current_app_logger.info(f"RID-{request_id_str}: KFP client default config prepared with token for Bearer auth.")
        else:
            current_app_logger.warning(f"RID-{request_id_str}: No token available (neither KFP_BEARER_TOKEN nor SA token from file). KFP calls might fail if authentication is required.")

        KFPConfiguration.set_default(default_kfp_config)
        
        client = KFPClient(host=KFP_ENDPOINT) 
        
        current_app_logger.info(f"RID-{request_id_str}: KFP Client (SDK v2 style) object created for host: {KFP_ENDPOINT}.")

        try:
            current_app_logger.info(f"RID-{request_id_str}: Verifying KFP client connection by listing experiments...")
            response = client.list_experiments(page_size=1) 
            exp_count = len(response.experiments) if response and hasattr(response, 'experiments') and response.experiments is not None else 0
            current_app_logger.info(f"RID-{request_id_str}: Successfully listed experiments (found {exp_count}). KFP API connection verified.")
        except kfp_server_api.ApiException as e_kfp_api: 
            current_app_logger.error(f"RID-{request_id_str}: KFP API call (list_experiments) failed: Status {e_kfp_api.status}, Reason: {e_kfp_api.reason}, Body: {e_kfp_api.body}", exc_info=False) 
        except Exception as verify_err:
            current_app_logger.warning(f"RID-{request_id_str}: KFP client created, but API verification call (list_experiments) failed: {str(verify_err)}.")
            if not isinstance(verify_err, kfp_server_api.ApiException): 
                 current_app_logger.error(f"RID-{request_id_str}: Full stack trace for KFP verification error:", exc_info=True)
        
        return client

    except Exception as e:
        current_app_logger.error(f"RID-{request_id_str}: Exception during KFP client initialization: {str(e)}", exc_info=True)
        return None

@app.route('/healthz', methods=['GET'])
def healthz():
    """Health check endpoint."""
    request_id = getattr(g, 'request_id', 'HEALTHZ_NO_G_ROUTE') 
    app.logger.debug(f"RID-{request_id}: Health check request received at /healthz")
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200
    

@app.route('/', methods=['POST'])
def handle_s3_event():
    """Handles incoming S3 events, parses them, and triggers a KFP pipeline."""
    request_id = getattr(g, 'request_id', "S3_EVENT_NO_G_ROUTE") 
    app.logger.info(f"RID-{request_id}: ==== POST / request received by KFP-S3-TRIGGER user-container ==== - {__file__}") # Added __file__ for context
    
    app.logger.info(f"RID-{request_id}: Request Content-Type: {request.content_type}")
    
    cloudevent_headers = { k.lower(): v for k, v in request.headers.items() if k.lower().startswith('ce-')}
    if cloudevent_headers:
        app.logger.info(f"RID-{request_id}: Received CloudEvent HTTP headers: {json.dumps(cloudevent_headers, indent=2)}")

    minio_event_data = None
    cloud_event_id_received = cloudevent_headers.get("ce-id", "N/A")

    try:
        if "application/cloudevents+json" in str(request.content_type).lower():
            full_payload = request.json
            app.logger.info(f"RID-{request_id}: Parsed as structured CloudEvent (application/cloudevents+json).")
            minio_event_data = full_payload.get('data') if isinstance(full_payload, dict) else None
            if isinstance(full_payload, dict) and 'id' in full_payload:
                 cloud_event_id_received = full_payload.get('id')
        elif cloudevent_headers.get("ce-specversion") and request.data:
            app.logger.info(f"RID-{request_id}: Detected binary CloudEvent mode based on ce-* headers and request data.")
            minio_event_data = json.loads(request.data.decode('utf-8'))
            app.logger.info(f"RID-{request_id}: Parsed request.data as JSON for binary CloudEvent data.")
        elif request.is_json: 
            minio_event_data = request.json
            app.logger.warning(f"RID-{request_id}: Request Content-Type was 'application/json' (not a CloudEvent). Assuming body is MinIO event data.")
        elif request.data : 
            try:
                minio_event_data = json.loads(request.data.decode('utf-8'))
                app.logger.warning(f"RID-{request_id}: Request data present, Content-Type not JSON. Attempted to parse as JSON.")
            except json.JSONDecodeError:
                app.logger.error(f"RID-{request_id}: Request data present but could not be parsed as JSON.")
                raw_body_sample_err = request.get_data(as_text=True)[:500]
                app.logger.debug(f"RID-{request_id}: Raw request body sample (first 500 chars for non-JSON): {raw_body_sample_err}")
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
             elif 'bucket' in minio_event_data and isinstance(minio_event_data.get('bucket'), dict) and 'name' in minio_event_data['bucket']:
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
        
        kfp_client = get_kfp_client()
        if not kfp_client:
            app.logger.error(f"RID-{request_id}: KFP client could not be initialized. Cannot trigger KFP pipeline.")
            return jsonify({"error": "KFP client initialization failed", "request_id": request_id}), 500

        app.logger.info(f"RID-{request_id}: Looking for KFP pipeline: '{PIPELINE_NAME}'")
        pipeline_id = None
        try:
            pipelines_response = kfp_client.list_pipelines(page_size=100, filter=json.dumps({"predicates": [{"key": "name", "op": "EQUALS", "string_value": PIPELINE_NAME}]}))
            if pipelines_response and pipelines_response.pipelines:
                pipeline_id = pipelines_response.pipelines[0].pipeline_id
            else:
                app.logger.error(f"RID-{request_id}: Pipeline '{PIPELINE_NAME}' not found using list_pipelines.")
        except Exception as e_get_id:
            app.logger.error(f"RID-{request_id}: Error getting KFP Pipeline ID for '{PIPELINE_NAME}': {str(e_get_id)}", exc_info=True)
            
        if not pipeline_id:
            app.logger.error(f"RID-{request_id}: Pipeline '{PIPELINE_NAME}' not found in KFP or error retrieving ID.")
            return jsonify({"error": f"Pipeline '{PIPELINE_NAME}' not found or error retrieving ID", "request_id": request_id}), 404
        app.logger.info(f"RID-{request_id}: Found KFP Pipeline ID: {pipeline_id}")

        experiment = None
        try:
            experiments_response = kfp_client.list_experiments(page_size=1, filter=json.dumps({"predicates": [{"key": "name", "op": "EQUALS", "string_value": KFP_EXPERIMENT_NAME}]}))
            if experiments_response and experiments_response.experiments:
                experiment = experiments_response.experiments[0]
                app.logger.info(f"RID-{request_id}: Using existing KFP Experiment ID: {experiment.experiment_id} (Name: {KFP_EXPERIMENT_NAME})")
            else:
                app.logger.warning(f"RID-{request_id}: Experiment '{KFP_EXPERIMENT_NAME}' not found. Attempting to create it.")
                experiment = kfp_client.create_experiment(experiment_name=KFP_EXPERIMENT_NAME) 
                app.logger.info(f"RID-{request_id}: Created KFP Experiment ID: {experiment.experiment_id} (Name: {KFP_EXPERIMENT_NAME})")
        except Exception as exp_e: 
            app.logger.error(f"RID-{request_id}: Failed to get or create KFP experiment '{KFP_EXPERIMENT_NAME}': {exp_e}", exc_info=True)
            return jsonify({"error": f"Failed to get or create KFP experiment: {exp_e}", "request_id": request_id}), 500
        
        pipeline_params = {
            'pdf_s3_bucket': bucket_name,
            'pdf_s3_key': object_key,
            'processing_timestamp': datetime.now(timezone.utc).isoformat()
        }
        app.logger.info(f"RID-{request_id}: Pipeline parameters for KFP run: {json.dumps(pipeline_params)}")

        run_name = f"PDF Event - {object_key.replace('/', '-').replace('.pdf', '')} - {request_id[:8]}"
        app.logger.info(f"RID-{request_id}: Submitting KFP run with name: {run_name}")
        
        run_result = kfp_client.create_run_from_pipeline_id(
            pipeline_id=pipeline_id,
            arguments=pipeline_params,
            run_name=run_name,
            experiment_name=KFP_EXPERIMENT_NAME 
        )
        
        run_id_str = getattr(run_result, 'run_id', "N/A") 
        run_name_str = getattr(run_result, 'name', run_name) 
        run_url = f"{KFP_ENDPOINT}/#/runs/details/{run_id_str}" if run_id_str != "N/A" else "N/A" 
        
        app.logger.info(f"RID-{request_id}: KFP run successfully triggered: ID={run_id_str}, Name='{run_name_str}', URL: {run_url}")
        
        return jsonify({
            "message": "KFP pipeline successfully triggered",
            "kfp_run_id": run_id_str,
            "kfp_run_url": run_url,
            "processed_bucket": bucket_name,
            "processed_key": object_key,
            "cloud_event_id_received": cloud_event_id_received,
            "request_id": request_id
        }), 200

    except json.JSONDecodeError as e_json:
        app.logger.error(f"RID-{request_id}: JSONDecodeError processing event: {str(e_json)}. Raw body sample (first 500 chars): {request.get_data(as_text=True)[:500]}", exc_info=True)
        return jsonify({"status": "error", "message": f"Invalid JSON in payload: {str(e_json)}", "request_id": request_id}), 400
    except Exception as e: 
        app.logger.error(f"RID-{request_id}: Global exception in handle_s3_event: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}", "request_id": request_id}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    flask_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't') 
    
    app.logger.info(f"=== S3 Event Handler Starting (Direct Flask Run) ===")
    app.run(host='0.0.0.0', port=port, debug=flask_debug_mode)