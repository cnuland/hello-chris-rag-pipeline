#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration Variables ---
# You can set these as environment variables before running the script,
# or the script will prompt you if they are not set.

# MinIO Server Details
: "${MINIO_SERVER_URL:?Error: MINIO_SERVER_URL is not set. Please provide the full URL (e.g., http://minio.example.com:9000 or https://minio-s3-minio.apps.your-cluster.com)}"
: "${MINIO_ACCESS_KEY:?Error: MINIO_ACCESS_KEY is not set. Please provide your MinIO access key.}"
: "${MINIO_SECRET_KEY:?Error: MINIO_SECRET_KEY is not set. Please provide your MinIO secret key.}"
: "${MC_ALIAS_NAME:=osminio}" # Default mc alias name if not set

# Target Bucket & Eventing Details
: "${TARGET_BUCKET_NAME:=pdf-inbox}" # Default bucket name if not set

# This KAFKA_TARGET_ID_IN_MINIO MUST EXACTLY MATCH the <YOUR_ID> suffix used in the
# MINIO_NOTIFY_KAFKA_ENABLE_<YOUR_ID>, MINIO_NOTIFY_KAFKA_BROKERS_<YOUR_ID>, etc.
# environment variables in your MinIO server's deployment.yaml.
# For example, if you used 'MYMINIOKAFKA' in those env vars, set this to "MYMINIOKAFKA".
: "${KAFKA_TARGET_ID_IN_MINIO:?Error: KAFKA_TARGET_ID_IN_MINIO is not set. This is the ID used in MinIO server's Kafka notification env vars (e.g., MYMINIOKAFKA).}"

# --- Script Logic ---

echo "--- MinIO Bucket Event Notification Setup Script (for server-configured Kafka target) ---"
echo ""
echo "Using the following configuration:"
echo "MinIO Server URL:        ${MINIO_SERVER_URL}"
echo "MinIO Access Key:        ${MINIO_ACCESS_KEY}"
echo "mc Alias Name:           ${MC_ALIAS_NAME}"
echo "Target S3 Bucket:        ${TARGET_BUCKET_NAME}"
echo "Kafka Target ID in MinIO: ${KAFKA_TARGET_ID_IN_MINIO}" # This is the ID like MYMINIOKAFKA
echo ""
echo "IMPORTANT: This script assumes your MinIO server is ALREADY configured"
echo "           via its deployment environment variables to know about the Kafka target"
echo "           identified by '${KAFKA_TARGET_ID_IN_MINIO}' (brokers, topic, etc.)."
echo ""
read -p "Is the above information correct and MinIO server properly configured? (yes/no) " confirmation
if [[ "$confirmation" != "yes" ]]; then
    echo "Aborted by user."
    exit 1
fi

# 1. Set up mc alias
echo ""
echo "Attempting to set up mc alias '${MC_ALIAS_NAME}'..."
if mc alias ls | grep -qF "${MC_ALIAS_NAME} "; then # Use -F for fixed string matching
    echo "Alias '${MC_ALIAS_NAME}' already exists. Assuming it's correctly configured."
else
    mc alias set ${MC_ALIAS_NAME} ${MINIO_SERVER_URL} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY} --api "S3v4"
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

# 3. Add event notification to the bucket, referencing the server-configured Kafka target
# The ARN format for a Kafka target configured via MinIO server environment variables
# typically uses the ID from those variables (e.g., MYMINIOKAFKA).
TARGET_ARN="arn:minio:kafka::${KAFKA_TARGET_ID_IN_MINIO}:"

echo ""
echo "Adding event notification to bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}'..."
echo "Using Target ARN (for server-configured Kafka target): ${TARGET_ARN}"
echo "Events: s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload"
echo "Suffix Filter: .pdf"

mc event add ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} "${TARGET_ARN}" \
    --event "s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload" \
    --suffix ".pdf" \
    --ignore-existing # This flag prevents errors if the exact same rule already exists

echo "Event notification rule for Kafka target added/updated."

# 4. Verify the event notification rule
echo ""
echo "Verifying event notification rules for bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' targeting ARN '${TARGET_ARN}':"
mc event list ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME} "${TARGET_ARN}"
echo ""
echo "Listing all event rules for the bucket:"
mc event list ${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}


echo ""
echo "--- MinIO Bucket Event Notification Setup Script Finished ---"
echo "If successful, MinIO should now send events for PDF uploads in '${TARGET_BUCKET_NAME}'"
echo "to the Kafka topic configured on the MinIO server for target ID '${KAFKA_TARGET_ID_IN_MINIO}'."
echo "Next, ensure your Knative KafkaSource is configured to consume from that Kafka topic."