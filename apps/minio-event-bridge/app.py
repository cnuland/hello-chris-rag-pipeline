from flask import Flask, request, jsonify
from cloudevents.http import CloudEvent, to_structured
import requests
import uuid
import logging
import os
from datetime import datetime, timezone # Ensure timezone is imported for UTC
import json # For pretty printing logs
import sys
app = Flask(__name__)

# Configure logging based on environment variable
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
# Set up root logger
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)

# Clear any existing handlers to avoid duplication if app reloads in some dev environments
if root_logger.handlers:
    root_logger.handlers.clear()

handler = logging.StreamHandler(sys.stdout) # Output to stdout for container logging
handler.setLevel(LOG_LEVEL)
# More detailed formatter for better context in logs
formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(lineno)d] %(message)s')
handler.setFormatter(formatter)
root_logger.addHandler(handler)

# Ensure Flask's own logger uses our configured handlers and level
# Note: Flask's default logger is app.logger. For gunicorn, it might use root logger by default.
# This ensures direct app.logger calls also use the configured format and level.
app.logger.handlers = root_logger.handlers
app.logger.setLevel(LOG_LEVEL)

logger = logging.getLogger(__name__) # Get a logger specific to this module
logger.info(f"Starting minio-event-bridge with LOG_LEVEL={LOG_LEVEL}")

# Define broker URL from environment variable or use default
BROKER_URL = os.environ.get('BROKER_URL', "http://kafka-broker-ingress.knative-eventing.svc.cluster.local/rag-pipeline-workshop/kafka-broker")
logger.info(f"Using broker URL: {BROKER_URL}")

@app.route('/health', methods=['GET'])
def health():
    logger.debug("Health check endpoint called")
    return jsonify({"status": "ok", "message": "minio-event-bridge is healthy"})

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    request_id = str(uuid.uuid4()) # For tracing this specific request
    logger.info(f"RID-{request_id}: Webhook request received. Method: {request.method}")
    
    if app.logger.isEnabledFor(logging.DEBUG): # Avoid logging headers unless DEBUG is on
        logger.debug(f"RID-{request_id}: Webhook request headers: {json.dumps(dict(request.headers), indent=2)}")
        if request.data:
            logger.debug(f"RID-{request_id}: Webhook raw request data (first 500B): {request.data[:500]}")
        
    # Handle GET requests (for health checks by some systems, validation, or debugging)
    if request.method == 'GET':
        logger.info(f"RID-{request_id}: Processing GET request to webhook endpoint.")
        return jsonify({
            "status": "ok",
            "message": "MinIO webhook endpoint is active. Send POST requests with MinIO notification payload.",
            "configured_broker_url": BROKER_URL 
        })
    
    # Handle POST requests (actual webhook notifications)
    try:
        if not request.is_json:
            if request.data:
                logger.warning(f"RID-{request_id}: Request Content-Type is '{request.content_type}', not 'application/json', but data is present. Attempting to parse as JSON.")
                minio_event = json.loads(request.data.decode('utf-8'))
            else:
                logger.error(f"RID-{request_id}: Request is not JSON and has no data.")
                return jsonify({"status": "error", "message": "Request body is not JSON or is empty"}), 400
        else:
            minio_event = request.json
        
        logger.info(f"RID-{request_id}: Received MinIO event payload: {json.dumps(minio_event, indent=2)}")
        
        # Extract event information to determine CloudEvent type
        event_type = "io.minio.object.unknown"  # A more specific default if EventName is missing
        
        # MinIO events often have 'EventName' at the top level or nested in 'Records'
        if 'EventName' in minio_event and minio_event['EventName']:
            event_type = minio_event['EventName'] # e.g., "s3:ObjectCreated:Put"
        elif 'Records' in minio_event and isinstance(minio_event['Records'], list) and len(minio_event['Records']) > 0:
            record = minio_event['Records'][0]
            if 'eventName' in record and record['eventName']:
                event_type = record['eventName']
        
        logger.info(f"RID-{request_id}: Using CloudEvent type: {event_type}")
        
        # Create CloudEvent attributes
        attributes = {
            "type": event_type,
            "source": "urn:minio:s3:::webhook", # More descriptive source URN
            "id": str(uuid.uuid4()),
            "time": datetime.now(timezone.utc).isoformat(), # Use timezone aware UTC time
            "datacontenttype": "application/json",
            "subject": None # Placeholder, can be set to object key if desired
        }
        
        # Extract object key for subject if possible
        object_key = None
        if 'Key' in minio_event:
            object_key = minio_event['Key']
        elif 'Records' in minio_event and isinstance(minio_event['Records'], list) and len(minio_event['Records']) > 0:
            record = minio_event['Records'][0]
            if 's3' in record and 'object' in record['s3'] and 'key' in record['s3']['object']:
                object_key = record['s3']['object']['key']
        
        if object_key:
            attributes["subject"] = object_key
            logger.info(f"RID-{request_id}: Set CloudEvent subject to: {object_key}")

        # Create CloudEvent object, with the full MinIO event as its data payload
        cloud_event = CloudEvent(attributes, minio_event)
        logger.debug(f"RID-{request_id}: Constructed CloudEvent object: {cloud_event}")
        
        # Convert to HTTP structured content (JSON format with CloudEvent headers)
        # This prepares a dict of HTTP headers and the CloudEvent JSON string as the body
        headers, body_bytes = to_structured(cloud_event) # body is bytes
        body_str = body_bytes.decode('utf-8')

        logger.info(f"RID-{request_id}: Attempting to POST CloudEvent to Broker URL: {BROKER_URL}")
        if app.logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"RID-{request_id}: CloudEvent HTTP Headers to be sent: {json.dumps(headers, indent=2)}")
            logger.debug(f"RID-{request_id}: CloudEvent HTTP Body to be sent: {body_str}")

        # Forward the CloudEvent to the Knative broker
        response = requests.post(
            BROKER_URL,
            headers=headers, # Contains 'Content-Type: application/cloudevents+json'
            data=body_bytes  # Send bytes directly
        )
        
        logger.info(f"RID-{request_id}: Broker Ingress Response Status: {response.status_code}")
        if app.logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"RID-{request_id}: Broker Ingress Response Headers: {json.dumps(dict(response.headers), indent=2)}")
            logger.debug(f"RID-{request_id}: Broker Ingress Response Body (first 500 chars): {response.text[:500]}")
        
        # Return status back to MinIO (or the caller of the webhook)
        return jsonify({
            "status": "success",
            "message": "Event forwarded to Knative broker",
            "cloud_event_id": attributes["id"],
            "broker_response_status": response.status_code
        }), 200 # Return 200 OK to MinIO if POST to broker was attempted
    
    except json.JSONDecodeError as e:
        logger.error(f"RID-{request_id}: JSONDecodeError processing webhook: {str(e)}. Raw data (first 500B): {request.data[:500]}", exc_info=True)
        return jsonify({"status": "error", "message": f"Invalid JSON payload: {str(e)}"}), 400
    except requests.exceptions.RequestException as e:
        logger.error(f"RID-{request_id}: HTTP RequestException forwarding to broker: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to connect to broker: {str(e)}"}), 503 # Service Unavailable for broker
    except Exception as e:
        logger.error(f"RID-{request_id}: Unexpected error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    # For local testing, Flask's built-in server with debug can be useful
    # Set FLASK_DEBUG=true or 1 as an environment variable for local debug mode
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ('true', '1', 't')
    
    app.logger.info(f"=== Starting MinIO Event Bridge (Local Flask Server) ===")
    app.logger.info(f"LOG_LEVEL: {LOG_LEVEL}")
    app.logger.info(f"BROKER_URL: {BROKER_URL}")
    app.logger.info(f"Flask server running on port: {port}")
    app.logger.info(f"Flask debug mode: {debug_mode}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)