import kfp
from kfp import dsl
from kfp.dsl import Input, Output, Artifact, InputPath, OutputPath

# Define a common base image that we will build using the Dockerfile provided next.
# This image will contain all necessary libraries for both components.
BASE_IMAGE = 'quay.io/cnuland/hello-chris-rag-json-pipeline:latest'

@dsl.component(
    base_image=BASE_IMAGE,
)
def fetch_incidents_from_api(
    api_endpoint: str,
    # Using the standard dsl.Output[dsl.Artifact] type for better compatibility.
    incidents_data: dsl.Output[dsl.Artifact]
):
    """Fetches closed-incident data from the mock ServiceNow API."""
    import requests
    import json
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.info(f"Starting API fetch component...")
    logging.info(f"Fetching data from endpoint: {api_endpoint}")
    
    # Parameters to fetch all closed incidents
    params = {'state': 'closed', 'limit': 200}
    
    try:
        response = requests.get(api_endpoint, params=params)
        logging.info(f"API response status code: {response.status_code}")
        response.raise_for_status()  # Raises an error for bad responses (4xx or 5xx)
        
        data = response.json()
        record_count = len(data.get('result', []))
        logging.info(f"Successfully fetched {record_count} incidents from API.")
        
        # Use the .path attribute to write to the KFP-managed artifact file
        logging.info(f"Writing data to artifact at path: {incidents_data.path}")
        with open(incidents_data.path, 'w') as f:
            json.dump(data, f, indent=2)
            
        logging.info(f"Successfully saved incident data.")
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data from API: {e}", exc_info=True)
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        raise

@dsl.component(
    base_image=BASE_IMAGE,
)
def ingest_incidents_to_milvus(
    # Using the standard dsl.Input[dsl.Artifact] type for better compatibility.
    incidents_data: dsl.Input[dsl.Artifact],
    milvus_host: str,
    milvus_port: str,
    collection_name: str = "servicenow_incidents",
):
    """Parses incident data, generates embeddings, and ingests into Milvus."""
    import json
    import logging
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    from sentence_transformers import SentenceTransformer

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting Milvus ingestion component...")

    # 1. Connect to Milvus using the provided service name and port
    logging.info(f"Attempting to connect to Milvus at {milvus_host}:{milvus_port}")
    try:
        connections.connect("default", host=milvus_host, port=milvus_port, timeout=10)
        logging.info("Successfully connected to Milvus.")
    except Exception as e:
        logging.error(f"Failed to connect to Milvus: {e}", exc_info=True)
        raise

    # 2. Define the collection schema
    embedding_dim = 384  # Based on the 'all-MiniLM-L6-v2' model
    
    fields = [
        FieldSchema(name="incident_pk", dtype=DataType.VARCHAR, is_primary=True, auto_id=False, max_length=20),
        FieldSchema(name="short_description", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="resolution_notes", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim)
    ]
    
    schema = CollectionSchema(fields, "ServiceNow Incidents Collection for RAG")
    
    # 3. Create the collection if it doesn't exist, dropping the old one for a fresh start
    if utility.has_collection(collection_name):
        logging.warning(f"Collection '{collection_name}' already exists. Dropping for a clean import.")
        utility.drop_collection(collection_name)
    logging.info(f"Creating collection: {collection_name}")
    collection = Collection(collection_name, schema)

    # 4. Load incident data and generate embeddings
    logging.info(f"Loading incident data from artifact at {incidents_data.path}...")
    try:
        with open(incidents_data.path, 'r') as f:
            data = json.load(f)
        logging.info("Successfully loaded data from artifact.")
    except Exception as e:
        logging.error(f"Failed to read or parse artifact file: {e}", exc_info=True)
        raise
    
    incidents = data.get('result', [])
    if not incidents:
        logging.warning("No incidents found in the 'result' key of the data. Exiting.")
        return

    logging.info("Loading sentence-transformer model 'all-MiniLM-L6-v2'...")
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    
    incident_pks = []
    short_descriptions = []
    resolution_notes_list = []
    embeddings = []
    
    logging.info(f"Preparing and embedding {len(incidents)} incidents...")
    for inc in incidents:
        if inc.get('resolution_notes'):
            incident_pks.append(inc['number'])
            short_descriptions.append(inc.get('short_description', ''))
            resolution_note = inc['resolution_notes']
            resolution_notes_list.append(resolution_note)
            text_to_embed = f"Title: {inc.get('short_description', '')}\nResolution: {resolution_note}"
            embeddings.append(model.encode(text_to_embed))

    if not incident_pks:
        logging.warning("No incidents with resolution notes were found to ingest. Exiting.")
        return
        
    # 5. Insert data into Milvus
    entities = [incident_pks, short_descriptions, resolution_notes_list, embeddings]
    
    logging.info(f"Inserting {len(incident_pks)} entities into Milvus...")
    try:
        insert_result = collection.insert(entities)
        collection.flush()
        logging.info(f"Successfully inserted entities. Mutation result: {insert_result}")
    except Exception as e:
        logging.error(f"Failed during Milvus insert/flush operation: {e}", exc_info=True)
        raise
    
    # 6. Create an index for efficient searching
    index_params = {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
    logging.info(f"Creating index with params: {index_params}")
    collection.create_index(field_name="embedding", index_params=index_params)
    collection.load()
    logging.info("Index created and collection loaded into memory. Component finished.")


@dsl.pipeline(
    name="API to Milvus RAG Ingestion Pipeline",
    description="Fetches incident data from an API and ingests it into a Milvus vector DB."
)
def api_to_milvus_pipeline(
    api_endpoint: str = "http://mock-servicenow-api-svc.user3.svc.cluster.local:8080/api/v1/incidents?state=closed",
    milvus_host: str = "vectordb-milvus",
    milvus_port: str = "19530",
    collection_name: str = "servicenow_incidents"
):
    # Task 1: Fetch data from the mock ServiceNow API
    fetch_task = fetch_incidents_from_api(
        api_endpoint=api_endpoint
    )
    fetch_task.set_display_name("Fetch ServiceNow Incidents")

    # Task 2: Ingest the fetched data into Milvus
    ingest_task = ingest_incidents_to_milvus(
        # Pass the output artifact from the fetch task to the ingest task
        incidents_data=fetch_task.output,
        milvus_host=milvus_host,
        milvus_port=milvus_port,
        collection_name=collection_name
    )
    ingest_task.set_display_name("Ingest Incidents to Milvus")


if __name__ == '__main__':
    # This section compiles the pipeline into a YAML file for upload.
    from kfp.compiler import Compiler
    Compiler().compile(
        pipeline_func=api_to_milvus_pipeline,
        package_path='api_to_milvus_pipeline.yaml'
    )
    print("Pipeline compiled to api_to_milvus_pipeline.yaml")