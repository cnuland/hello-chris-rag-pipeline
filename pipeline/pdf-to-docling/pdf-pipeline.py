from kfp import dsl
from kfp.dsl import Output, Artifact
import kfp.compiler

# Define a simple component using the component decorator
@dsl.component(
    base_image='registry.redhat.io/ubi9/ubi-minimal:latest'  # A minimal image
)
def process_pdf_op(
    pdf_s3_bucket: str,
    pdf_s3_key: str,
    processing_timestamp: str,
    summary_output: Output[Artifact]
) -> None:
    """Process a PDF file (stub implementation).
    
    Args:
        pdf_s3_bucket: The S3 bucket containing the PDF
        pdf_s3_key: The S3 key of the PDF
        processing_timestamp: The timestamp when processing was triggered
        summary_output: Output artifact containing a summary of the processing
    """
    import os
    
    # Log the received information
    print("Received PDF for processing:")
    print(f"Bucket: {pdf_s3_bucket}")
    print(f"Key: {pdf_s3_key}")
    print(f"Event Timestamp: {processing_timestamp}")
    print("This is a stub. Actual PDF processing would happen here.")
    
    # Create a summary output
    with open(summary_output.path, 'w') as f:
        f.write(f"Processed: s3://{pdf_s3_bucket}/{pdf_s3_key}\n")
    
    print(f"Summary written to {summary_output.path}")

# Define a component for handling pipeline exit
@dsl.component(
    base_image='registry.redhat.io/ubi9/ubi-minimal:latest'
)
def pipeline_exit_op(status: str, pipeline_name: str) -> None:
    """Log pipeline exit status.
    
    Args:
        status: The pipeline execution status
        pipeline_name: The name of the pipeline
    """
    print(f"Pipeline {pipeline_name} finished with status: {status}")

@dsl.pipeline(
    name='Simple PDF Processing Pipeline',
    description='A stub pipeline triggered by PDF upload to S3, runs via Argo on KFP.'
)
def simple_pdf_processing_pipeline(
    pdf_s3_bucket: str = 'default-bucket',
    pdf_s3_key: str = 'default/path/to/file.pdf',
    processing_timestamp: str = ''
):
    """
    A simple pipeline that takes an S3 path for a PDF and logs it.
    
    Args:
        pdf_s3_bucket: The S3 bucket containing the PDF
        pdf_s3_key: The S3 key of the PDF
        processing_timestamp: The timestamp when processing was triggered
    """
    # Execute the PDF processing task
    pdf_task = process_pdf_op(
        pdf_s3_bucket=pdf_s3_bucket,
        pdf_s3_key=pdf_s3_key,
        processing_timestamp=processing_timestamp
    )
    
    # You can add more steps here that depend on pdf_task.outputs['summary_output']
    
    # Example of using an exit handler (commented out for now)
    # To implement this in KFP v2, we would use:
    # with dsl.ExitHandler(pipeline_exit_op(status="Completed", pipeline_name="PDF Pipeline")):
    #     pdf_task = process_pdf_op(...)

if __name__ == '__main__':
    # This will compile the pipeline into a .yaml file
    kfp.compiler.Compiler().compile(
        pipeline_func=simple_pdf_processing_pipeline,
        package_path='simple_pdf_pipeline.yaml'
    )
    print("Simple PDF pipeline compiled to simple_pdf_pipeline.yaml")
