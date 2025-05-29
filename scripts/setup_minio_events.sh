#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration Variables ---
# You can set these as environment variables before running the script,
# or the script will prompt you if they are not set.

# MinIO Server Details
: "${MINIO_SERVER_URL:?Error: MINIO_SERVER_URL is not set. Please provide the full URL (e.g., http://minio.example.com:9000 or https://minio-route.apps.cluster.com)}"
: "${MINIO_ACCESS_KEY:?Error: MINIO_ACCESS_KEY is not set. Please provide your MinIO access key.}"
: "${MINIO_SECRET_KEY:?Error: MINIO_SECRET_KEY is not set. Please provide your MinIO secret key.}"
: "${MC_ALIAS_NAME:=osminio}" # Default mc alias name if not set

# Target Bucket & Eventing Details
: "${TARGET_BUCKET_NAME:=pdf-inbox}" # Default bucket name if not set
: "${SERVERLESS_NAMESPACE:?Error: SERVERLESS_NAMESPACE is not set. Please provide the OpenShift namespace where minio-cloudevents-service runs (e.g., rag-pipeline-workshop).}"
: "${MINIO_EVENT_WEBHOOK_SHARED_SECRET:?Error: MINIO_EVENT_WEBHOOK_SHARED_SECRET is not set. This must match the secret used by minio-cloudevents-service.}"
: "${WEBHOOK_TARGET_ID_IN_MINIO:=knativeeventbridge}" # Identifier for the webhook configuration within MinIO

# --- Script Logic ---

echo "--- MinIO Event Notification Setup Script ---"
echo ""
echo "Using the following configuration:"
echo "MinIO Server URL:             ${MINIO_SERVER_URL}"
echo "MinIO Access Key:             ${MINIO_ACCESS_KEY}"
echo "mc Alias Name:                ${MC_ALIAS_NAME}"
echo "Target S3 Bucket:             ${TARGET_BUCKET_NAME}"
echo "OpenShift Serverless Namespace: ${SERVERLESS_NAMESPACE}"
echo "Webhook Target ID in MinIO:   ${WEBHOOK_TARGET_ID_IN_MINIO}"
echo "Webhook Shared Secret:        (hidden)"
echo ""
read -p "Is the above information correct? (yes/no) " confirmation
if [[ "$confirmation" != "yes" ]]; then
    echo "Aborted by user."
    exit 1
fi

# 1. Set up mc alias
echo ""
echo "Attempting to set up mc alias '${MC_ALIAS_NAME}'..."
if mc alias ls | grep -q "^${MC_ALIAS_NAME} "; then
    echo "Alias '${MC_ALIAS_NAME}' already exists. Assuming it's correctly configured."
    # Optionally, you could remove and re-add, but that might require re-entering keys if not from env.
    # mc alias remove ${MC_ALIAS_NAME}
    # mc alias set ${MC_ALIAS_NAME} ${MINIO_SERVER_URL} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}
else
    mc alias set ${MC_ALIAS_NAME} ${MINIO_SERVER_URL} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}
    echo "Alias '${MC_ALIAS_NAME}' created successfully."
fi

# 2. Create the target bucket if it doesn't exist
echo ""
echo "Checking/Creating S3 bucket '${TARGET_BUCKET_NAME}' on alias '${MC_ALIAS_NAME}'..."
if mc ls ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} > /dev/null 2>&1; then
    echo "Bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' already exists."
else
    mc mb ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}
    echo "Bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' created successfully."
fi

# 3. Register the webhook service endpoint with MinIO Admin
# This is the internal Kubernetes service URL for minio-cloudevents-service
EVENT_BRIDGE_INTERNAL_ENDPOINT="http://minio-cloudevents-service.${SERVERLESS_NAMESPACE}.svc.cluster.local:8080"

echo ""
echo "Attempting to add/update event notification for bucket '${MC_ALIAS_NAME}/${MINIO_BUCKET}'..."
echo "Target Webhook Endpoint: ${EVENT_BRIDGE_INTERNAL_ENDPOINT}"
echo "Events: s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload"
echo "Suffix Filter: .pdf"

# This syntax attempts to define the webhook target directly within mc event add
mc event add ${MC_ALIAS_NAME}/${MINIO_BUCKET} webhook \
    --endpoint "${EVENT_BRIDGE_INTERNAL_ENDPOINT}" \
    --auth-token "${MINIO_EVENT_WEBHOOK_SHARED_SECRET}" \
    --event "s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload" \
    --suffix ".pdf" \
    --ignore-existing # This flag prevents errors if the exact same rule already exists

echo "Event notification rule command executed."
echo "Verifying event notification rules for bucket '${MC_ALIAS_NAME}/${MINIO_BUCKET}':"
# To list, we can try listing all events for the bucket or a specific ARN if we knew it.
# Since we're not registering a service ID anymore with this approach,
# we might not have a simple ARN. Listing all events for the bucket is more reliable here.
mc event list ${MC_ALIAS_NAME}/${MINIO_BUCKET}

# 4. Add event notification to the bucket
# Construct the ARN for the webhook target.
# This assumes the WEBHOOK_TARGET_ID_IN_MINIO is used directly in the ARN.
# If 'mc admin service list' shows a different effective ID (e.g., numeric), adjust TARGET_ARN.
TARGET_ARN="arn:minio:webhook::${WEBHOOK_TARGET_ID_IN_MINIO}:"

echo ""
echo "Adding event notification to bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}'..."
echo "Using Target ARN: ${TARGET_ARN}"
echo "Events: s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload"
echo "Suffix Filter: .pdf"

mc event add ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} "${TARGET_ARN}" \
    --event "s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload" \
    --suffix ".pdf" \
    --ignore-existing # This flag prevents errors if the exact same rule already exists

echo "Event notification rule added (or updated if identical rule existed)."

# 5. Verify the event notification rule
echo ""
echo "Verifying event notification rules for bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' (look for ARN '${TARGET_ARN}'):"
mc event list ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} "${TARGET_ARN}" # List specific rule
echo ""
mc event list ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} # List all rules for the bucket


echo ""
echo "--- MinIO Event Notification Setup Script Finished ---"
echo "Please test by uploading a PDF to the '${TARGET_BUCKET_NAME}' bucket."
echo "Monitor logs of 'minio-cloudevents-service' and 'kfp-s3-trigger' (your Knative service)."