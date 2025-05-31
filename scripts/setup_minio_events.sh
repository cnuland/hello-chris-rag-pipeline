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

# This WEBHOOK_TARGET_ID_IN_MINIO MUST EXACTLY MATCH the <YOUR_ID> suffix used in the
# MINIO_NOTIFY_WEBHOOK_ENABLE_<YOUR_ID>, MINIO_NOTIFY_WEBHOOK_ENDPOINT_<YOUR_ID>, etc.
# environment variables in your MinIO server's deployment.yaml.
# For example, if you used 'HTTPBRIDGE' in those env vars, set this to "HTTPBRIDGE".
: "${WEBHOOK_TARGET_ID_IN_MINIO:=RAG}" # Default to RAG as used in the deployment.yaml

# --- Script Logic ---

echo "--- MinIO Bucket Event Notification Setup Script (for Webhook Target) ---"
echo ""
echo "Using the following configuration:"
echo "MinIO Server URL:             ${MINIO_SERVER_URL}"
echo "MinIO Access Key:             ${MINIO_ACCESS_KEY}"
echo "mc Alias Name:                ${MC_ALIAS_NAME}"
echo "Target S3 Bucket:             ${TARGET_BUCKET_NAME}"
echo "Webhook Target ID in MinIO:   ${WEBHOOK_TARGET_ID_IN_MINIO}"
echo ""
echo "IMPORTANT: This script assumes your MinIO server is ALREADY configured"
echo "           via its deployment environment variables to know about the Webhook target"
echo "           identified by '${WEBHOOK_TARGET_ID_IN_MINIO}' (endpoint URL, auth token if any)."
echo ""
read -p "Is the above information correct and MinIO server properly configured for this webhook target? (yes/no) " confirmation
if [[ "$confirmation" != "yes" ]]; then
    echo "Aborted by user."
    exit 1
fi

# 1. Set up mc alias
echo ""
echo "Attempting to set up mc alias '${MC_ALIAS_NAME}'..."
if mc alias ls --insecure | grep -qF "${MC_ALIAS_NAME} "; then # Use -F for fixed string matching
    echo "Alias '${MC_ALIAS_NAME}' already exists. Assuming it's correctly configured."
else
    mc alias set "${MC_ALIAS_NAME}" "${MINIO_SERVER_URL}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --api "S3v4" --insecure
    echo "Alias '${MC_ALIAS_NAME}' created successfully."
fi

# 2. Create the target bucket if it doesn't exist
echo ""
echo "Checking/Creating S3 bucket '${TARGET_BUCKET_NAME}' on alias '${MC_ALIAS_NAME}'..."
if mc ls "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" --insecure > /dev/null 2>&1; then
    echo "Bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' already exists."
else
    mc mb "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" --insecure
    echo "Bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' created successfully."
fi

# 3. Construct the ARN for the server-configured Webhook target
# The ARN format for a Webhook target configured via MinIO server environment variables
# uses the ID from those variables (e.g., HTTPBRIDGE).
TARGET_ARN="arn:minio:sqs::${WEBHOOK_TARGET_ID_IN_MINIO}:webhook"

echo ""
echo "Adding event notification to bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}'..."
echo "Using Target ARN (for server-configured Webhook target): ${TARGET_ARN}"
echo "Events: put (file creation/upload)"
echo "Suffix Filter: .pdf"

echo "Adding event rule..."
mc event add "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" "${TARGET_ARN}" \
    --event put \
    --suffix ".pdf" \
    --insecure
    # The --ignore-existing flag can be useful if the rule with these exact filters already exists
    # mc event add "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" "${TARGET_ARN}" \
    #    --event "s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload" \
    #    --suffix ".pdf" \
    #    --ignore-existing

echo "Event notification rule for Webhook target added/updated."

# 4. Verify the event notification rule
echo ""
echo "Verifying event notification rules for bucket '${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}' targeting ARN '${TARGET_ARN}':"
mc event list "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" "${TARGET_ARN}" --insecure
echo ""
echo "Listing all event rules for the bucket:"
mc event list "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" --insecure


echo ""
echo "--- MinIO Bucket Event Notification Setup Script Finished ---"
echo "If successful, MinIO should now send events for PDF uploads in '${TARGET_BUCKET_NAME}'"
echo "to the Webhook endpoint configured on the MinIO server for target ID '${WEBHOOK_TARGET_ID_IN_MINIO}'."
echo "This webhook should be your Python bridge service (e.g., minio-webhook-bridge)."