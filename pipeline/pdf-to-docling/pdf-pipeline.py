import kfp
from kfp import dsl
from kfp.dsl import Input, Output, Artifact

# This should be the container image you build using the provided Dockerfile.
# Ensure it's pushed to a registry that your OpenShift cluster can access.
BASE_IMAGE = 'quay.io/cnuland/docling-pipeline:latest'

@dsl.component(
    base_image=BASE_IMAGE,
)
def download_pdf_from_s3(
    s3_bucket: str,
    s3_key: str,
    s3_endpoint_url: str,
    s3_access_key: str,
    s3_secret_key: str,
    downloaded_pdf_file: dsl.Output[dsl.Artifact]
):
    """Downloads a specific PDF file from an S3 bucket."""
    import boto3
    import logging

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Log all incoming parameters for easier debugging
    logging.info(f"Received S3 Bucket: {s3_bucket}")
    logging.info(f"Received S3 Key: {s3_key}")
    logging.info(f"Received S3 Endpoint URL: {s3_endpoint_url}")

    if not all([s3_endpoint_url, s3_access_key, s3_secret_key]):
        logging.error("One or more S3 connection parameters were empty.")
        raise ValueError("Missing S3 configuration parameters. Please provide s3_endpoint_url, s3_access_key, and s3_secret_key.")

    logging.info(f"Attempting to download s3://{s3_bucket}/{s3_key}")
    
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=s3_endpoint_url,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
        )
        s3_client.download_file(s3_bucket, s3_key, downloaded_pdf_file.path)
        logging.info(f"Successfully downloaded PDF to artifact path: {downloaded_pdf_file.path}")
    except Exception as e:
        logging.error(f"Error downloading from S3: {e}", exc_info=True)
        raise

@dsl.component(
    base_image=BASE_IMAGE,
)
def process_pdf_with_docling(
    pdf_artifact: dsl.Input[dsl.Artifact],
    docling_output_json: dsl.Output[dsl.Artifact]
):
    """Processes a PDF file using Docling and outputs structured JSON."""
    import pathlib
    import logging
    from docling.document_converter import DocumentConverter

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    pdf_input_path = pathlib.Path(pdf_artifact.path)
    output_json_path = pathlib.Path(docling_output_json.path)
    
    logging.info(f"Processing PDF: {pdf_input_path.name} with docling...")
    logging.info(f"Docling output will be saved to: {output_json_path}")

    doc_converter = DocumentConverter()
    conv_results = doc_converter.convert_all([pdf_input_path], raises_on_error=True)

    if conv_results and conv_results[0].document:
        conv_res = conv_results[0]
        logging.info(f"Docling successfully parsed document. Status: {conv_res.status}")
        conv_res.document.save_as_json(output_json_path)
        logging.info("Successfully saved docling output as JSON artifact.")
    else:
        raise RuntimeError(f"Docling conversion failed or returned no document for {pdf_input_path.name}")

@dsl.component(
    base_image=BASE_IMAGE,
)
def ingest_docling_output_to_milvus(
    docling_json: dsl.Input[dsl.Artifact],
    milvus_host: str,
    milvus_port: str,
    collection_name: str,
    document_id: str, # e.g., the original S3 key
):
    """Ingests processed document text into a Milvus collection."""
    import json
    import logging
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    from sentence_transformers import SentenceTransformer

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    logging.info(f"Starting Milvus ingestion for document: {document_id}")
    logging.info(f"Attempting to connect to Milvus at {milvus_host}:{milvus_port}")
    try:
        connections.connect("default", host=milvus_host, port=milvus_port, timeout=10)
        logging.info("Successfully connected to Milvus.")
    except Exception as e:
        logging.error(f"Failed to connect to Milvus: {e}", exc_info=True)
        raise

    embedding_dim = 384
    fields = [
        FieldSchema(name="doc_pk", dtype=DataType.VARCHAR, is_primary=True, auto_id=False, max_length=1024),
        FieldSchema(name="text_chunk", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim)
    ]
    schema = CollectionSchema(fields, "Parsed PDF Document Collection (Docling)")

    if not utility.has_collection(collection_name):
        logging.info(f"Collection '{collection_name}' does not exist. Creating it now.")
        collection = Collection(collection_name, schema)
    else:
        logging.info(f"Using existing collection: '{collection_name}'")
        collection = Collection(collection_name)

    logging.info(f"Loading docling JSON from artifact at {docling_json.path}")
    with open(docling_json.path, 'r') as f:
        doc_data = json.load(f)

    # Extract all text content from the parsed document
    full_text = " ".join([p['text'] for p in doc_data.get('paragraphs', [])])
    
    if not full_text:
        logging.warning("No text found in the parsed document. Exiting.")
        return

    logging.info("Loading sentence-transformer model 'all-MiniLM-L6-v2'...")
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')

    logging.info("Generating embedding for the document text...")
    embedding = model.encode(full_text)

    entities = [
        [document_id], # Primary key
        [full_text],   # The full text content
        [embedding]    # The vector embedding
    ]
    
    logging.info(f"Inserting 1 entity into Milvus for document '{document_id}'...")
    try:
        insert_result = collection.insert(entities)
        collection.flush()
        logging.info(f"Successfully inserted entity. Mutation result: {insert_result}")
    except Exception as e:
        logging.error(f"Failed during Milvus insert/flush operation: {e}", exc_info=True)
        raise

    # Ensure index exists for searching
    if not collection.has_index():
        index_params = {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
        logging.info(f"Creating index with params: {index_params}")
        collection.create_index(field_name="embedding", index_params=index_params)
        logging.info("Index created.")
    
    collection.load()
    logging.info("Collection loaded into memory. Component finished.")


@dsl.pipeline(
    name="PDF to Milvus Ingestion Pipeline",
    description="Downloads a PDF from S3, parses with Docling, and ingests into Milvus."
)
def pdf_to_milvus_pipeline(
    s3_bucket: str = "pdf-inbox",
    s3_key: str = "example.pdf",
    s3_endpoint_url: str = "http://minio-service.minio.svc.cluster.local:9000",
    s3_access_key: str = "minio",
    s3_secret_key: str = "minio123",
    milvus_host: str = "vectordb-milvus",
    milvus_port: str = "19530",
    collection_name: str = "docling_pdf_collection"
):
    # Task 1: Download the PDF from S3
    download_task = download_pdf_from_s3(
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key
    )
    download_task.set_display_name("Download PDF from S3")

    # Task 2: Process the downloaded PDF with Docling
    docling_task = process_pdf_with_docling(
        pdf_artifact=download_task.output
    )
    docling_task.set_display_name("Process PDF with Docling")

    # Task 3: Ingest the structured JSON into Milvus
    ingest_task = ingest_docling_output_to_milvus(
        docling_json=docling_task.outputs['docling_output_json'],
        milvus_host=milvus_host,
        milvus_port=milvus_port,
        collection_name=collection_name,
        document_id=s3_key # Use the S3 key as a unique identifier
    )
    ingest_task.set_display_name("Ingest Document to Milvus")


if __name__ == '__main__':
    # This section compiles the pipeline into a YAML file for upload.
    from kfp.compiler import Compiler
    Compiler().compile(
        pipeline_func=pdf_to_milvus_pipeline,
        package_path='pdf_to_milvus_pipeline.yaml'
    )
    print("Pipeline compiled to pdf_to_milvus_pipeline.yaml")