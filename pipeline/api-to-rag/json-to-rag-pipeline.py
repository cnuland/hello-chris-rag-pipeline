import os
import json
import logging
import requests
from typing import Optional, List

import kfp
from kfp import dsl
from kfp.dsl import Input, Output, Artifact, component, pipeline


@component(
    base_image="quay.io/cnuland/hello-chris-rag-json-pipeline:latest",
    packages_to_install=[
        "requests",
        "pymilvus",
        "sentence-transformers",
        "marshmallow>=3.13.0"  # ✅ Fix the version issue here
    ])
def fetch_incidents_from_api(
    api_endpoint: str,
    incidents_data: Output[Artifact]
) -> None:
    """Fetches incidents from the mock API and stores them as a JSON artifact."""
    import json
    import logging
    import os
    import requests
    from typing import Optional, List
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    from sentence_transformers import SentenceTransformer

    logging.basicConfig(level=logging.INFO)
    logging.info(f"Calling API: {api_endpoint}")

    try:
        response = requests.get(api_endpoint)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logging.error(f"Failed to fetch incidents: {e}", exc_info=True)
        raise

    os.makedirs(os.path.dirname(incidents_data.path), exist_ok=True)
    with open(incidents_data.path, "w") as f:
        json.dump(data, f, indent=2)

    logging.info(f"Wrote incidents to {incidents_data.path}")


@component(
    base_image="quay.io/cnuland/hello-chris-rag-json-pipeline:latest",
    packages_to_install=["pymilvus", "sentence-transformers"]
)
def ingest_incidents_to_milvus(
    incidents_data: Input[Artifact],
    milvus_host: str,
    milvus_port: str,
    collection_name: str = "servicenow_incidents",
) -> None:
    """Parses incident data, generates embeddings, and ingests into Milvus."""
    import json
    import logging
    import os
    import requests
    from typing import Optional, List
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    from sentence_transformers import SentenceTransformer

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting ingestion to Milvus...")

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
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim),
    ]
    schema = CollectionSchema(fields, "ServiceNow Incidents Collection for RAG")

    if utility.has_collection(collection_name):
        logging.warning(f"Collection {collection_name} exists. Dropping...")
        utility.drop_collection(collection_name)
    collection = Collection(collection_name, schema)

    try:
        with open(incidents_data.path, "r") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Failed to read artifact: {e}", exc_info=True)
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


@pipeline(name="api-to-milvus-rag-ingestion-pipeline", description="Fetch data and embed into Milvus for RAG")
def api_to_milvus_pipeline(
    api_endpoint: str,
    milvus_host: str,
    milvus_port: str,
    collection_name: str = "servicenow_incidents"
):
    fetch_task = fetch_incidents_from_api(api_endpoint=api_endpoint)
    ingest_task = ingest_incidents_to_milvus(
        incidents_data=fetch_task.outputs["incidents_data"],
        milvus_host=milvus_host,
        milvus_port=milvus_port,
        collection_name=collection_name,
    )


if __name__ == "__main__":
    from kfp import compiler

    compiler.Compiler().compile(
        pipeline_func=api_to_milvus_pipeline,
        package_path="api_to_milvus_pipeline.yaml"
    )
