import os
import json
import logging
import kfp
from kfp import dsl
from kfp.dsl import Input, Output, Artifact

BASE_IMAGE = 'quay.io/cnuland/hello-chris-rag-json-pipeline:latest'

@dsl.component(base_image=BASE_IMAGE)
def fetch_incidents_from_api(
    api_endpoint: str,
    incidents_data: Output[Artifact],
):
    """Fetches closed-incident data from a mock ServiceNow API and writes to artifact."""
    import requests

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting fetch_incidents_from_api...")
    logging.info(f"API endpoint: {api_endpoint}")
    
    params = {'state': 'closed', 'limit': 200}
    try:
        response = requests.get(api_endpoint, params=params)
        logging.info(f"API status: {response.status_code}")
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logging.error(f"Failed to fetch data from API: {e}", exc_info=True)
        data = {}

    # Fallback if no data
    if not data.get("result"):
        logging.warning("API returned no incidents. Using fallback dummy data.")
        data = {
            "result": [
                {
                    "number": "INC00001",
                    "short_description": "System rebooted unexpectedly",
                    "resolution_notes": "Issue resolved by updating kernel."
                }
            ]
        }

    try:
        logging.info(f"Writing data to: {incidents_data.path}")
        with open(incidents_data.path, "w") as f:
            json.dump(data, f, indent=2)
        logging.info("Incident data written successfully.")
        logging.info(f"Output dir listing: {os.listdir(os.path.dirname(incidents_data.path))}")
    except Exception as e:
        logging.error(f"Failed to write artifact: {e}", exc_info=True)
        raise

@dsl.component(base_image=BASE_IMAGE)
def ingest_incidents_to_milvus(
    incidents_data: Input[Artifact],
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

    try:
        connections.connect("default", host=milvus_host, port=milvus_port)
        logging.info(f"Connected to Milvus at {milvus_host}:{milvus_port}")
    except Exception as e:
        logging.error(f"Connection to Milvus failed: {e}", exc_info=True)
        raise

    embedding_dim = 384
    fields = [
        FieldSchema(name="incident_pk", dtype=DataType.VARCHAR, is_primary=True, auto_id=False, max_length=20),
        FieldSchema(name="short_description", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="resolution_notes", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim)
    ]
    schema = CollectionSchema(fields, "ServiceNow Incidents Collection for RAG")

    if utility.has_collection(collection_name):
        logging.warning(f"Collection {collection_name} exists. Dropping...")
        utility.drop_collection(collection_name)
    collection = Collection(collection_name, schema)

    try:
        logging.info(f"Reading data from {incidents_data.path}")
        with open(incidents_data.path, "r") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load artifact: {e}", exc_info=True)
        raise

    incidents = data.get("result", [])
    if not incidents:
        logging.warning("No incidents to process.")
        return

    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    records = [
        (
            inc["number"],
            inc.get("short_description", ""),
            inc["resolution_notes"],
            model.encode(f"Title: {inc.get('short_description', '')}\nResolution: {inc['resolution_notes']}")
        )
        for inc in incidents if inc.get("resolution_notes")
    ]

    if not records:
        logging.warning("No valid incidents with resolution notes found.")
        return

    incident_pks, short_descriptions, resolution_notes_list, embeddings = zip(*records)
    entities = [list(incident_pks), list(short_descriptions), list(resolution_notes_list), list(embeddings)]

    try:
        insert_result = collection.insert(entities)
        collection.flush()
        logging.info(f"Inserted {len(incident_pks)} records.")
    except Exception as e:
        logging.error(f"Failed to insert into Milvus: {e}", exc_info=True)
        raise

    index_params = {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
    collection.create_index("embedding", index_params)
    collection.load()
    logging.info("Index created and collection loaded.")

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
    fetch_task = fetch_incidents_from_api(api_endpoint=api_endpoint)
    fetch_task.set_display_name("Fetch ServiceNow Incidents")

    ingest_task = ingest_incidents_to_milvus(
        incidents_data=fetch_task.outputs["incidents_data"],
        milvus_host=milvus_host,
        milvus_port=milvus_port,
        collection_name=collection_name
    )
    ingest_task.set_display_name("Ingest Incidents to Milvus")

if __name__ == "__main__":
    from kfp.compiler import Compiler
    Compiler().compile(
        pipeline_func=api_to_milvus_pipeline,
        package_path="api_to_milvus_pipeline.yaml"
    )
    print("✅ Pipeline compiled: api_to_milvus_pipeline.yaml")
