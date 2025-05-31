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

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        logger.debug(f"Received webhook request with headers: {dict(request.headers)}")
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
        
        # Forward the CloudEvent to the Knative broker
        response = requests.post(
            BROKER_URL,
            headers=headers,
            data=body
        )
        
        logger.info(f"Forwarded CloudEvent to broker, response: {response.status_code}")
        logger.debug(f"Broker response details: {response.text}")
        
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

