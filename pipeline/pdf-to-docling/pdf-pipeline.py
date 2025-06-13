import kfp
from kfp.dsl import InputPath, OutputPath, component, pipeline
import os
import json
import logging

BASE_IMAGE = 'quay.io/cnuland/docling-pipeline:latest'

@component(
    base_image=BASE_IMAGE,
    packages_to_install=["boto3==1.28.57"]
)
def download_pdf_from_s3(
    s3_bucket: str,
    s3_key: str,
    s3_endpoint_url: str,
    s3_access_key: str,
    s3_secret_key: str,
    downloaded_pdf_file_path: OutputPath(str)
) -> None:
    import boto3
    import logging
    import os

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    logging.info(f"Attempting to download s3://{s3_bucket}/{s3_key}")
    logging.info(f"Artifact path: {downloaded_pdf_file_path}")

    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=s3_endpoint_url,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
        )
        os.makedirs(os.path.dirname(downloaded_pdf_file_path), exist_ok=True)
        s3_client.download_file(s3_bucket, s3_key, downloaded_pdf_file_path)

        if not os.path.exists(downloaded_pdf_file_path):
            raise FileNotFoundError(f"Expected artifact file not found at {downloaded_pdf_file_path}")

        logging.info(f"Successfully downloaded PDF to artifact path: {downloaded_pdf_file_path}")
    except Exception as e:
        logging.error(f"Error downloading from S3: {e}", exc_info=True)
        raise

@component(
    base_image=BASE_IMAGE,
    packages_to_install=["docling"]
)
def process_pdf_with_docling(
    pdf_artifact_path: InputPath(str),
    docling_output_json_path: OutputPath(str)
) -> None:
    import pathlib
    import logging
    import json
    import os
    import binascii
    from docling.document_converter import DocumentConverter

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    pdf_input_path = pathlib.Path(pdf_artifact_path)
    output_json_path = pathlib.Path(docling_output_json_path)

    logging.info(f"Processing PDF: {pdf_input_path.name} with docling...")
    logging.info(f"Docling output will be saved to: {output_json_path}")

    doc_converter = DocumentConverter()
    conv_results = doc_converter.convert_all([pdf_input_path], raises_on_error=True)

    if conv_results and conv_results[0].document:
        conv_res = conv_results[0]
        logging.info(f"Docling successfully parsed document. Status: {conv_res.status}")
        doc_dict = conv_res.document.save_as_dict()

        logging.info(f"Top-level keys: {list(doc_dict.keys())}")
        logging.info(f"Data types of keys: {[type(v).__name__ for v in doc_dict.values()]}")

        try:
            cleaned_dict = json.loads(json.dumps(doc_dict, ensure_ascii=False))
        except Exception as json_sanitize_err:
            logging.error(f"Error sanitizing JSON: {json_sanitize_err}", exc_info=True)
            raise

        os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned_dict, f, ensure_ascii=False, indent=2)

        try:
            with open(output_json_path, 'rb') as f:
                raw_bytes = f.read()
                raw_bytes.decode('utf-8')
                logging.info(f"Output file size: {len(raw_bytes)} bytes")
                logging.info(f"Output file preview (hex): {binascii.hexlify(raw_bytes[:256]).decode()}...")
        except Exception as encoding_err:
            logging.error(f"Output file failed UTF-8 validation: {encoding_err}", exc_info=True)
            raise

        try:
            json_preview = json.dumps(cleaned_dict, ensure_ascii=False)[:1000]
            logging.info(f"Docling JSON preview (first 1K chars): {json_preview}")
        except Exception as preview_err:
            logging.warning(f"Failed to preview JSON: {preview_err}", exc_info=True)

        logging.info("Successfully saved docling output as JSON artifact.")
    else:
        raise RuntimeError(f"Docling conversion failed for {pdf_input_path.name}")

@pipeline(
    name="pdf-to-docling-debug-pipeline",
    description="Downloads a PDF from S3 and parses it with Docling."
)
def pdf_to_docling_pipeline(
    s3_bucket: str,
    s3_key: str,
    s3_endpoint_url: str,
    s3_access_key: str,
    s3_secret_key: str
):
    download_task = download_pdf_from_s3(
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key
    )
    download_task.set_display_name("Download PDF from S3")
    download_task.set_caching_options(False)

    docling_task = process_pdf_with_docling(
        pdf_artifact_path=download_task.outputs["downloaded_pdf_file_path"]
    )
    docling_task.set_display_name("Process PDF with Docling")
    docling_task.set_caching_options(False)

if __name__ == '__main__':
    from kfp.compiler import Compiler
    Compiler().compile(
        pipeline_func=pdf_to_docling_pipeline,
        package_path='pdf_to_docling_debug_pipeline.yaml'
    )
    print("Pipeline compiled to pdf_to_docling_debug_pipeline.yaml")
