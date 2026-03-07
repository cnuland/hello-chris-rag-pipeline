import os
import json
import uuid
import logging
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, has_request_context

# KFP SDK v2.x imports (for kfp==2.7.0 compatibility)
from kfp import Client as KFPClient
import kfp_server_api
import urllib3  # For disabling InsecureRequestWarning

# --- Environment Variables ---
# Default KFP endpoint — used as fallback for instructor demo (shared pdf-inbox bucket)
KFP_ENDPOINT = os.environ.get("KFP_ENDPOINT")
KFP_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

PIPELINE_NAME = os.environ.get("KFP_PIPELINE_NAME", "simple-pdf-processing-pipeline")
KFP_EXPERIMENT_NAME = os.environ.get("KFP_EXPERIMENT_NAME", "S3 Triggered PDF Runs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
KFP_VERIFY_SSL = os.environ.get("KFP_VERIFY_SSL", "true").lower() == "true"
REQUESTS_CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE", "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")

# Multi-user routing config
# Suffix appended to username to form bucket name (e.g., "user1" + "-pdf-inbox" = "user1-pdf-inbox")
USER_BUCKET_SUFFIX = os.environ.get("USER_BUCKET_SUFFIX", "-pdf-inbox")
# The default/instructor bucket name (no user prefix)
DEFAULT_BUCKET_NAME = os.environ.get("DEFAULT_BUCKET_NAME", "pdf-inbox")
# KFP DSPA service name pattern — {namespace} will be replaced with the user's namespace
KFP_DSPA_PATTERN = os.environ.get("KFP_DSPA_PATTERN", "https://ds-pipeline-dspa.{namespace}.svc.cluster.local:8443")


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
app.logger.info(f"KFP_ENDPOINT (default/fallback): {KFP_ENDPOINT if KFP_ENDPOINT else 'NOT SET'}")
app.logger.info(f"KFP_PIPELINE_NAME: {PIPELINE_NAME}")
app.logger.info(f"KFP_EXPERIMENT_NAME: {KFP_EXPERIMENT_NAME}")
app.logger.info(f"KFP_VERIFY_SSL: {KFP_VERIFY_SSL}")
app.logger.info(f"REQUESTS_CA_BUNDLE path: {REQUESTS_CA_BUNDLE}")
app.logger.info(f"USER_BUCKET_SUFFIX: {USER_BUCKET_SUFFIX}")
app.logger.info(f"DEFAULT_BUCKET_NAME: {DEFAULT_BUCKET_NAME}")
app.logger.info(f"KFP_DSPA_PATTERN: {KFP_DSPA_PATTERN}")


def _read_sa_token():
    """Read the service account token from the mounted file."""
    if os.path.exists(KFP_SA_TOKEN_PATH):
        try:
            with open(KFP_SA_TOKEN_PATH, 'r') as f:
                return f.read().strip()
        except Exception as e:
            app.logger.error(f"Could not read SA token from {KFP_SA_TOKEN_PATH}: {e}")
    else:
        app.logger.warning(f"SA token path {KFP_SA_TOKEN_PATH} not found.")
    return None


def resolve_kfp_endpoint(bucket_name, request_id):
    """Determine the correct KFP endpoint based on the source bucket name.

    - Per-user bucket (e.g., 'user1-pdf-inbox'): route to user1's DSPA
    - Default bucket ('pdf-inbox'): route to KFP_ENDPOINT env var (instructor demo)
    """
    if not bucket_name or bucket_name == DEFAULT_BUCKET_NAME:
        # Instructor demo mode — use the default KFP_ENDPOINT
        app.logger.info(f"RID-{request_id}: Bucket '{bucket_name}' is the default instructor bucket. "
                        f"Using fallback KFP_ENDPOINT: {KFP_ENDPOINT}")
        return KFP_ENDPOINT

    # Check if bucket matches the per-user pattern: {username}{suffix}
    if bucket_name.endswith(USER_BUCKET_SUFFIX):
        username = bucket_name[:-len(USER_BUCKET_SUFFIX)]
        if username:
            endpoint = KFP_DSPA_PATTERN.format(namespace=username)
            app.logger.info(f"RID-{request_id}: Bucket '{bucket_name}' -> user '{username}' -> "
                            f"KFP endpoint: {endpoint}")
            return endpoint

    # Unrecognized bucket pattern — fall back to default
    app.logger.warning(f"RID-{request_id}: Bucket '{bucket_name}' does not match expected pattern "
                       f"'*{USER_BUCKET_SUFFIX}'. Falling back to KFP_ENDPOINT: {KFP_ENDPOINT}")
    return KFP_ENDPOINT


def extract_event_details(request_data, request_id):
    """Extract bucket name and object key from the CloudEvent body (MinIO event payload)."""
    bucket_name = None
    object_key = None

    try:
        event_data = json.loads(request_data) if isinstance(request_data, (str, bytes)) else request_data
    except (json.JSONDecodeError, TypeError) as e:
        app.logger.warning(f"RID-{request_id}: Could not parse event body as JSON: {e}")
        return None, None

    # Try Records array first (standard S3 notification format)
    if 'Records' in event_data and isinstance(event_data['Records'], list) and len(event_data['Records']) > 0:
        record = event_data['Records'][0]
        if 's3' in record:
            if 'bucket' in record['s3'] and 'name' in record['s3']['bucket']:
                bucket_name = record['s3']['bucket']['name']
            if 'object' in record['s3'] and 'key' in record['s3']['object']:
                object_key = record['s3']['object']['key']

    # Fallback: MinIO top-level 'Key' field (format: "bucket/objectkey")
    if not bucket_name and 'Key' in event_data:
        key_parts = event_data['Key'].split('/', 1)
        if len(key_parts) == 2:
            bucket_name = key_parts[0]
            object_key = object_key or key_parts[1]

    # Also check CloudEvent header for bucket name (set by minio-event-bridge)
    ce_bucket = request.headers.get('Ce-Bucketname')
    if ce_bucket and not bucket_name:
        bucket_name = ce_bucket

    app.logger.info(f"RID-{request_id}: Extracted event details — bucket: '{bucket_name}', object_key: '{object_key}'")
    return bucket_name, object_key


def get_kfp_client(endpoint, request_id):
    """Initialize and return a KFP client for the given endpoint."""
    if not endpoint:
        app.logger.error(f"RID-{request_id}: No KFP endpoint available. Cannot create client.")
        return None

    app.logger.info(f"RID-{request_id}: Initializing KFP Client for endpoint: {endpoint}")

    sa_token = _read_sa_token()

    ssl_ca_cert_to_use = None
    if endpoint.startswith('https://'):
        if KFP_VERIFY_SSL:
            if os.path.exists(REQUESTS_CA_BUNDLE):
                ssl_ca_cert_to_use = REQUESTS_CA_BUNDLE
                app.logger.info(f"RID-{request_id}: SSL verification ENABLED with CA: {ssl_ca_cert_to_use}")
            else:
                app.logger.warning(f"RID-{request_id}: SSL verification ENABLED but CA bundle '{REQUESTS_CA_BUNDLE}' not found.")
        else:
            app.logger.warning(f"RID-{request_id}: SSL verification DISABLED.")
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        client_kwargs = {
            'host': endpoint,
            'existing_token': sa_token,
        }
        if endpoint.startswith('https://'):
            client_kwargs['ssl_ca_cert'] = ssl_ca_cert_to_use

        client = KFPClient(**client_kwargs)
        app.logger.info(f"RID-{request_id}: KFP Client created for: {endpoint}")
        return client

    except Exception as e:
        app.logger.error(f"RID-{request_id}: KFP client initialization failed: {e}", exc_info=True)
        return None


@app.before_request
def before_request_logging():
    g.request_id = str(uuid.uuid4())
    app.logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG):
        headers_dict = dict(request.headers)
        if "Authorization" in headers_dict:
            headers_dict["Authorization"] = "***MASKED***"
        app.logger.debug(f"RID-{g.request_id}: Request headers: {json.dumps(headers_dict, indent=2)}")

        if request.data:
            try:
                body_sample = request.get_data(as_text=True)[:500] + ('...' if len(request.data) > 500 else '')
            except Exception:
                body_sample = "[Could not decode body]"
            app.logger.debug(f"RID-{g.request_id}: Request body sample: {body_sample}")


@app.after_request
def after_request_logging(response):
    request_id = getattr(g, 'request_id', 'NO_REQUEST_ID')
    response_sample = response.get_data(as_text=True)[:200] + ('...' if len(response.get_data(as_text=True)) > 200 else '')
    app.logger.debug(f"RID-{request_id}: Response status: {response.status_code}, body: {response_sample}")
    return response


@app.route('/healthz', methods=['GET'])
def healthz():
    return jsonify(status="healthy", message="kfp-s3-trigger is running"), 200


@app.route('/', methods=['POST'])
def handle_s3_event():
    request_id = getattr(g, 'request_id', "S3_EVENT_NO_G")
    app.logger.info(f"RID-{request_id}: ==== POST / received — processing S3 event ====")

    # --- Step 1: Extract event details from the CloudEvent body ---
    raw_body = request.get_data(as_text=True)
    bucket_name, object_key = extract_event_details(raw_body, request_id)

    # --- Step 2: Resolve the correct KFP endpoint based on bucket ---
    kfp_endpoint = resolve_kfp_endpoint(bucket_name, request_id)
    if not kfp_endpoint:
        msg = "Could not determine KFP endpoint. Check KFP_ENDPOINT env var and bucket naming."
        app.logger.error(f"RID-{request_id}: {msg}")
        return jsonify({"status": "error", "message": msg, "request_id": request_id}), 500

    # --- Step 3: Initialize KFP client for the resolved endpoint ---
    kfp_client = get_kfp_client(kfp_endpoint, request_id)
    if not kfp_client:
        return jsonify({"status": "error", "message": "KFP client initialization failed",
                        "request_id": request_id}), 500

    try:
        # --- Step 4: Find the pipeline by name ---
        app.logger.info(f"RID-{request_id}: Looking for pipeline named '{PIPELINE_NAME}'...")
        pipelines = kfp_client.list_pipelines()
        pipeline_id = None
        if pipelines and pipelines.pipelines:
            for p in pipelines.pipelines:
                if p.display_name == PIPELINE_NAME:
                    pipeline_id = p.pipeline_id
                    app.logger.info(f"RID-{request_id}: Found pipeline '{PIPELINE_NAME}' (ID: {pipeline_id})")
                    break

        if not pipeline_id:
            msg = f"Pipeline named '{PIPELINE_NAME}' not found at {kfp_endpoint}"
            app.logger.error(f"RID-{request_id}: {msg}")
            return jsonify({"status": "error", "message": msg, "request_id": request_id}), 404

        # --- Step 5: Get latest pipeline version ---
        versions = kfp_client.list_pipeline_versions(pipeline_id=pipeline_id)
        if not versions or not versions.pipeline_versions:
            msg = f"No versions found for pipeline ID {pipeline_id}"
            app.logger.error(f"RID-{request_id}: {msg}")
            return jsonify({"status": "error", "message": msg, "request_id": request_id}), 404

        version_id = versions.pipeline_versions[0].pipeline_version_id
        app.logger.info(f"RID-{request_id}: Using pipeline version: {version_id}")

        # --- Step 6: Create or get experiment ---
        experiment = kfp_client.create_experiment(name=KFP_EXPERIMENT_NAME)
        app.logger.info(f"RID-{request_id}: Using experiment '{KFP_EXPERIMENT_NAME}' "
                        f"(ID: {experiment.experiment_id})")

        # --- Step 7: Build run parameters ---
        run_params = {}
        if object_key:
            run_params["s3_object_key"] = object_key
        if bucket_name:
            run_params["s3_bucket_name"] = bucket_name

        job_name = f"s3-trigger-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        app.logger.info(f"RID-{request_id}: Starting pipeline run '{job_name}' with params: {run_params}")

        # --- Step 8: Run the pipeline ---
        run = kfp_client.run_pipeline(
            experiment_id=experiment.experiment_id,
            job_name=job_name,
            pipeline_id=pipeline_id,
            version_id=version_id,
            params=run_params,
        )

        if run:
            app.logger.info(f"RID-{request_id}: Pipeline run started successfully. "
                            f"Run ID: {run.run_id}, Endpoint: {kfp_endpoint}")
            return jsonify({
                "status": "success",
                "message": "Pipeline run started",
                "run_id": run.run_id,
                "pipeline": PIPELINE_NAME,
                "endpoint": kfp_endpoint,
                "bucket": bucket_name,
                "object_key": object_key,
                "request_id": request_id,
            }), 200
        else:
            app.logger.warning(f"RID-{request_id}: run_pipeline returned None.")
            return jsonify({"status": "error", "message": "run_pipeline returned None",
                            "request_id": request_id}), 500

    except kfp_server_api.ApiException as e:
        app.logger.error(f"RID-{request_id}: KFP API error: Status {e.status}, Reason: {e.reason}", exc_info=False)
        app.logger.debug(f"RID-{request_id}: KFP API Exception Body: {e.body}")
        return jsonify({"status": "error", "message": f"KFP API error: {e.reason}",
                        "details": str(e.body), "request_id": request_id}), 500
    except Exception as e:
        app.logger.error(f"RID-{request_id}: Unexpected error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal error: {e}",
                        "request_id": request_id}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    flask_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't')
    app.logger.info(f"=== S3 Event Handler Starting (Multi-User Routing Enabled) ===")
    app.run(host='0.0.0.0', port=port, debug=flask_debug_mode)
