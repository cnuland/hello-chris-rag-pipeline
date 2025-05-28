from kfp.dsl import pipeline, ContainerOp, ExitHandler
import kfp.components as comp # For creating components from functions

# Define a simple component (can also be a pre-built container image)
# This component will just print the S3 path
def process_pdf_op(pdf_s3_bucket: str, pdf_s3_key: str, processing_timestamp: str):
    return ContainerOp(
        name='Process PDF Stub',
        image='registry.redhat.io/ubi9/ubi-minimal:latest', # A minimal image
        command=['sh', '-c'],
        arguments=[
            f'echo "Received PDF for processing:" && \
             echo "Bucket: {pdf_s3_bucket}" && \
             echo "Key: {pdf_s3_key}" && \
             echo "Event Timestamp: {processing_timestamp}" && \
             echo "This is a stub. Actual PDF processing would happen here." && \
             echo "Outputting a dummy artifact..." && \
             mkdir -p /tmp/outputs && \
             echo "Processed: s3://{pdf_s3_bucket}/{pdf_s3_key}" > /tmp/outputs/summary.txt'
        ],
        file_outputs={'summary': '/tmp/outputs/summary.txt'} # Example of an output artifact
    )

# Define a component for handling pipeline exit (success or failure)
def pipeline_exit_op(status: str, pipeline_name: str):
    return ContainerOp(
        name='Pipeline Exit Handler',
        image='registry.redhat.io/ubi9/ubi-minimal:latest',
        command=['sh', '-c'],
        arguments=[
            f'echo "Pipeline {pipeline_name} finished with status: {status}"'
        ]
    )


@pipeline(
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
    """
    # pipeline_name_param = "{{workflow.name}}" # Accesses Argo workflow name for context

    # Using ExitHandler to demonstrate cleanup or notification step
    # with ExitHandler(exit_op=pipeline_exit_op(status="{{workflow.status}}", pipeline_name=pipeline_name_param)):
    pdf_task = process_pdf_op(
        pdf_s3_bucket=pdf_s3_bucket,
        pdf_s3_key=pdf_s3_key,
        processing_timestamp=processing_timestamp
    )
    # You can add more steps here that depend on pdf_task.outputs['summary'] for example

if __name__ == '__main__':
    from kfp.compiler import Compiler
    # This will compile the pipeline into a .tar.gz file (or .yaml if specified)
    # This compiled file needs to be uploaded to your Kubeflow Pipelines instance.
    Compiler().compile(simple_pdf_processing_pipeline, 'simple_pdf_pipeline.tar.gz')
    print("Simple PDF pipeline compiled to simple_pdf_pipeline.tar.gz")