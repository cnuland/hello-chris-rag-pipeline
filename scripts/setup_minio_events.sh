#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# MinIO Server Details
: "${MINIO_SERVER_URL:?Error: MINIO_SERVER_URL is not set}"
: "${MINIO_ACCESS_KEY:?Error: MINIO_ACCESS_KEY is not set}"
: "${MINIO_SECRET_KEY:?Error: MINIO_SECRET_KEY is not set}"
: "${MC_ALIAS_NAME:=osminio}"
: "${TARGET_BUCKET_NAME:=pdf-inbox}"
: "${KAFKA_TARGET_ID_IN_MINIO:?Error: KAFKA_TARGET_ID_IN_MINIO is not set}"

echo "Using the following configuration:"
echo "MinIO Server URL:        ${MINIO_SERVER_URL}"
echo "MinIO Access Key:        ${MINIO_ACCESS_KEY}"
echo "mc Alias Name:           ${MC_ALIAS_NAME}"
echo "Target S3 Bucket:        ${TARGET_BUCKET_NAME}"
echo "Kafka Target ID in MinIO: ${KAFKA_TARGET_ID_IN_MINIO}"

# 1. Set up mc alias
mc alias set "${MC_ALIAS_NAME}" "${MINIO_SERVER_URL}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --api "S3v4"

# 2. Create the target bucket if it doesn't exist
if ! mc ls "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" > /dev/null 2>&1; then
    mc mb "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}"
fi

# 3. Add event notification using the correct ARN format
ARN="arn:minio:sqs::MYMINIOKAFKA:kafka"
echo "Attempting to add notification with ARN: ${ARN}"

mc event add "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}" "${ARN}" \
    --event "put" \
    --suffix ".pdf"

# 4. Verify the event notification rule
echo "Listing configured events:"
mc event list "${MC_ALIAS_NAME}/${TARGET_BUCKET_NAME}"
