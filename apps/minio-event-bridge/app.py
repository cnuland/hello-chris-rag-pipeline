from flask import Flask, request, jsonify
from cloudevents.http import CloudEvent, to_structured
import requests
import uuid
import logging
import os
from datetime import datetime
import json

app = Flask(__name__)

# Configure logging based on environment variable
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)
logger.info(f"Starting minio-event-bridge with LOG_LEVEL={LOG_LEVEL}")

# Define broker URL from environment variable or use default
BROKER_URL = os.environ.get('BROKER_URL', "http://kafka-broker-ingress.knative-eventing.svc.cluster.local/rag-pipeline-workshop/kafka-broker")
logger.info(f"Using broker URL: {BROKER_URL}")

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    try:
        logger.info(f"Received webhook request with method: {request.method}")
        logger.debug(f"Received webhook request with headers: {dict(request.headers)}")
        
        # Handle GET requests (for health checks, validation, or debugging)
        if request.method == 'GET':
            logger.info("Processing GET request to webhook endpoint")
            return jsonify({
                "status": "ok",
                "message": "MinIO webhook endpoint is active and ready to receive event notifications",
                "usage": "Send POST requests with MinIO notification payload to this endpoint",
                "broker_url": BROKER_URL
            })
        
        # Handle POST requests (actual webhook notifications)
        # Get the event data from MinIO
        minio_event = request.json
        logger.info(f"Received MinIO event: {json.dumps(minio_event, indent=2)}")
        
        # Extract event information
        event_type = "s3:ObjectCreated:Put"  # Default type
        
        # Try to extract the actual event type if available in the event
        if 'EventName' in minio_event:
            event_type = minio_event['EventName']
            logger.debug(f"Found EventName: {event_type}")
        elif 'Records' in minio_event and len(minio_event['Records']) > 0:
            if 'eventName' in minio_event['Records'][0]:
                event_type = minio_event['Records'][0]['eventName']
                logger.debug(f"Found eventName in Records: {event_type}")
        
        logger.info(f"Extracted event type: {event_type}")
        
        # Create a CloudEvent
        attributes = {
            "type": event_type,
            "source": "minio:s3",
            "id": str(uuid.uuid4()),
            "time": datetime.utcnow().isoformat() + "Z",
            "datacontenttype": "application/json",
        }
        
        # Create CloudEvent with MinIO event as data
        cloud_event = CloudEvent(attributes, minio_event)
        logger.debug(f"Created CloudEvent with attributes: {attributes}")
        
        # Convert to HTTP structured content
        headers, body = to_structured(cloud_event)
        logger.debug(f"CloudEvent headers: {headers}")
        
        logger.info(f"Attempting to POST CloudEvent to Broker. URL: {BROKER_URL}")
        logger.info(f"CloudEvent HTTP Headers being sent: {json.dumps(headers, indent=2)}") # headers from to_structured()
        logger.info(f"CloudEvent HTTP Body being sent: {body.decode('utf-8') if isinstance(body, bytes) else body}") # body from to_structured()

        response = requests.post(
            BROKER_URL,
            headers=headers,
            data=body
        )
        logger.info(f"Broker Ingress Response Status: {response.status_code}")
        logger.info(f"Broker Ingress Response Headers: {json.dumps(dict(response.headers), indent=2)}")
        logger.info(f"Broker Ingress Response Body (first 500 chars): {response.text[:500]}")
        
        # Return status back to MinIO
        return jsonify({
            "status": "success",
            "message": "Event forwarded to Knative broker",
            "broker_response": response.status_code
        })
    
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

