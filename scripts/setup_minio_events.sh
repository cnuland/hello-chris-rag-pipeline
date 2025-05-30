# --- Define these variables (replace with your actual values) ---
MC_ALIAS="osminio" # Your mc alias
TARGET_BUCKET_NAME="pdf-inbox"
# This ID MUST match the suffix you used in the MINIO_NOTIFY_KAFKA_ENABLE_* environment variables
KAFKA_TARGET_ID_IN_MINIO="MYMINIOKAFKA"
# --- End of variables ---

# Construct the ARN for the server-configured Kafka target
TARGET_ARN="arn:minio:kafka::${KAFKA_TARGET_ID_IN_MINIO}:"

echo "Adding event notification to bucket '${MC_ALIAS}/${TARGET_BUCKET_NAME}' for Kafka target..."
echo "Using Target ARN: ${TARGET_ARN}"

mc event add ${MC_ALIAS}/${TARGET_BUCKET_NAME} "${TARGET_ARN}" \
    --event "s3:ObjectCreated:Put,s3:ObjectCreated:CompleteMultipartUpload" \
    --suffix ".pdf" \
    --ignore-existing

echo "Event notification rule for Kafka target added."
echo "Verifying event rules for bucket:"
mc event list ${MC_ALIAS}/${TARGET_BUCKET_NAME} "${TARGET_ARN}"