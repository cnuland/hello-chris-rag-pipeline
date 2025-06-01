from flask import Flask, request, jsonify, g # Added g for request_id consistency
from cloudevents.http import CloudEvent # CloudEvent object
from cloudevents.conversion import to_binary, to_structured # to_binary is key
from cloudevents.http import marshaller # For more control if needed, but to_binary is often enough
import requests
import uuid
import logging
import os
from datetime import datetime, timezone
import json
import sys

app = Flask(__name__)

# Configure logging based on environment variable
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
root_logger = logging.getLogger()
try:
    effective_log_level = getattr(logging, LOG_LEVEL)
except AttributeError:
    print(f"Warning: Invalid LOG_LEVEL '{LOG_LEVEL}'. Defaulting to INFO.", file=sys.stderr)
    effective_log_level = logging.INFO
root_logger.setLevel(effective_log_level)

if root_logger.handlers:
    root_logger.handlers.clear()
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(effective_log_level)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(lineno)d] %(message)s') # Basic formatter
handler.setFormatter(formatter)
root_logger.addHandler(handler)

app.logger.handlers = root_logger.handlers
app.logger.setLevel(effective_log_level)
gunicorn_error_logger = logging.getLogger('gunicorn.error')
gunicorn_error_logger.handlers = root_logger.handlers
gunicorn_error_logger.setLevel(effective_log_level)
gunicorn_access_logger = logging.getLogger('gunicorn.access')
gunicorn_access_logger.handlers = root_logger.handlers
gunicorn_access_logger.setLevel(effective_log_level)

logger = logging.getLogger(__name__)
logger.info(f"Starting minio-event-bridge with LOG_LEVEL={LOG_LEVEL}")

BROKER_URL = os.environ.get('BROKER_URL', "http://kafka-broker-ingress.knative-eventing.svc.cluster.local/rag-pipeline-workshop/kafka-broker")
logger.info(f"Using broker URL: {BROKER_URL}")

@app.before_request
def before_request_logging():
    g.request_id = str(uuid.uuid4())
    logger.debug(f"RID-{g.request_id}: Request received: {request.method} {request.path} from {request.remote_addr}")
    if app.logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"RID-{g.request_id}: Webhook request headers: {json.dumps(dict(request.headers), indent=2)}")
        if request.data:
            logger.debug(f"RID-{g.request_id}: Webhook raw request data (first 500B): {request.data[:500]}")

@app.after_request
def after_request_logging(response):
    request_id = getattr(g, 'request_id', 'no-request-id')
    logger.debug(f"RID-{request_id}: Response status: {response.status_code}")
    return response

@app.route('/health', methods=['GET'])
def health():
    logger.debug("Health check endpoint called")
    return jsonify({"status": "ok", "message": "minio-event-bridge is healthy"})

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    request_id = getattr(g, 'request_id', str(uuid.uuid4()))
    logger.info(f"RID-{request_id}: Webhook processing started. Method: {request.method}")
        
    if request.method == 'GET':
        logger.info(f"RID-{request_id}: Processing GET request to webhook endpoint.")
        return jsonify({
            "status": "ok",
            "message": "MinIO webhook endpoint is active. Send POST requests with MinIO notification payload.",
            "configured_broker_url": BROKER_URL 
        })
    
    try:
        minio_event = None
        if request.is_json:
            minio_event = request.json
        elif request.data:
            logger.warning(f"RID-{request_id}: Request Content-Type is '{request.content_type}', not 'application/json', but data present. Attempting to parse data as JSON.")
            minio_event = json.loads(request.data.decode('utf-8'))
        else:
            logger.error(f"RID-{request_id}: Request is not JSON and has no data.")
            return jsonify({"status": "error", "message": "Request body is not JSON or is empty"}), 400
        
        logger.info(f"RID-{request_id}: Received MinIO event payload: {json.dumps(minio_event, indent=2)}")
        
        event_type = "io.minio.object.unknown"
        if 'EventName' in minio_event and minio_event['EventName']:
            event_type = minio_event['EventName']
        elif 'Records' in minio_event and isinstance(minio_event['Records'], list) and len(minio_event['Records']) > 0:
            record = minio_event['Records'][0]
            if 'eventName' in record and record['eventName']:
                event_type = record['eventName']
        
        logger.info(f"RID-{request_id}: Using CloudEvent type: {event_type}")
        
        attributes = {
            "type": event_type,
            "source": "urn:minio:s3:::webhook", # Or "minio:s3" if you prefer
            "id": str(uuid.uuid4()),
            "time": datetime.now(timezone.utc).isoformat(),
            "datacontenttype": "application/json", # Type of the 'data' field
            "subject": None
        }
        
        object_key = None
        if 'Key' in minio_event: # MinIO specific top-level key
            object_key = minio_event['Key']
        elif 'Records' in minio_event and isinstance(minio_event['Records'], list) and len(minio_event['Records']) > 0:
            record = minio_event['Records'][0]
            if 's3' in record and 'object' in record['s3'] and 'key' in record['s3']['object']:
                object_key = record['s3']['object']['key']
        
        if object_key:
            attributes["subject"] = object_key
            logger.info(f"RID-{request_id}: Set CloudEvent subject to: {object_key}")

        # Create CloudEvent object with MinIO event as data
        # The 'data' (minio_event) should be a dict if datacontenttype is application/json
        cloud_event_obj = CloudEvent(attributes, minio_event)
        logger.debug(f"RID-{request_id}: Constructed CloudEvent object: {cloud_event_obj}")
        
        # --- MODIFICATION: Convert to HTTP Binary Mode ---
        # to_binary returns a dict of headers and the body (which is the data payload)
        # The body needs to be encoded if it's not bytes, matching datacontenttype.
        # Since datacontenttype is application/json, data should be JSON-serializable.
        # The CloudEvents SDK's to_binary handles making sure the data is in byte form.
        
        binary_headers, binary_body_data = to_binary(cloud_event_obj, data_marshaller=json.dumps)
        # data_marshaller=json.dumps ensures the 'data' (minio_event) is serialized to a JSON string
        # before being encoded to bytes, if it's not already bytes.
        # The 'Content-Type' in binary_headers will be "application/json" (from datacontenttype).
        # ce-* headers will also be in binary_headers.

        logger.info(f"RID-{request_id}: Attempting to POST CloudEvent (Binary Mode) to Broker URL: {BROKER_URL}")
        if app.logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"RID-{request_id}: CloudEvent (Binary) HTTP Headers to be sent: {json.dumps(binary_headers, indent=2)}")
            # binary_body_data from to_binary is already bytes
            logger.debug(f"RID-{request_id}: CloudEvent (Binary) HTTP Body to be sent (data payload): {binary_body_data.decode('utf-8') if isinstance(binary_body_data, bytes) else str(binary_body_data)[:500]}")

        response = requests.post(
            BROKER_URL,
            headers=binary_headers, # Contains 'ce-*' attributes and 'Content-Type: application/json'
            data=binary_body_data   # Contains only the MinIO JSON payload (as bytes)
        )
        
        logger.info(f"RID-{request_id}: Broker Ingress Response Status: {response.status_code}")
        if app.logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"RID-{request_id}: Broker Ingress Response Headers: {json.dumps(dict(response.headers), indent=2)}")
            logger.debug(f"RID-{request_id}: Broker Ingress Response Body (first 500 chars): {response.text[:500]}")
        
        return jsonify({
            "status": "success",
            "message": "Event forwarded to Knative broker (binary mode)",
            "cloud_event_id": attributes["id"],
            "broker_response_status": response.status_code
        }), 200
    
    except json.JSONDecodeError as e:
        logger.error(f"RID-{request_id}: JSONDecodeError processing webhook: {str(e)}. Raw data (first 500B): {request.data[:500]}", exc_info=True)
        return jsonify({"status": "error", "message": f"Invalid JSON payload: {str(e)}"}), 400
    except requests.exceptions.RequestException as e:
        logger.error(f"RID-{request_id}: HTTP RequestException forwarding to broker: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to connect to broker: {str(e)}"}), 503
    except Exception as e:
        logger.error(f"RID-{request_id}: Unexpected error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't')
    
    logger.info(f"=== Starting MinIO Event Bridge (Local Flask Server / Direct Execution) ===")
    logger.info(f"LOG_LEVEL: {LOG_LEVEL}")
    logger.info(f"BROKER_URL: {BROKER_URL}")
    logger.info(f"Flask server running on host 0.0.0.0, port: {port}")
    logger.info(f"Flask debug mode: {debug_mode}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)