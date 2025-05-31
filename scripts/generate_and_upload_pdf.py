#!/usr/bin/env python3
"""
Script to generate a simple PDF with a timestamp and upload it to MinIO.
Uses environment variables for MinIO configuration.
"""

import os
import sys
import subprocess
import datetime
import uuid
import tempfile
from pathlib import Path

# Check if reportlab is installed, and install it if not
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
except ImportError:
    print("ReportLab not found. Please install it manually with:")
    print("python3 -m pip install reportlab --user")
    sys.exit(1)

def generate_pdf(output_path):
    """Generate a simple PDF with current timestamp."""
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    unique_id = str(uuid.uuid4())[:8]
    
    try:
        c = canvas.Canvas(str(output_path), pagesize=letter)
        c.setFont("Helvetica", 12)
        
        # Add timestamp and unique ID
        c.drawString(100, 750, f"Timestamp: {current_time}")
        c.drawString(100, 730, f"Unique ID: {unique_id}")
        
        # Add additional content
        c.drawString(100, 700, "This is a test PDF for MinIO event notifications")
        c.drawString(100, 680, "Generated for testing Knative event processing")
        
        c.save()
        print(f"✅ PDF generated successfully: {output_path}")
        return True
    except Exception as e:
        print(f"❌ Error generating PDF: {e}")
        return False

def configure_mc():
    """Configure MinIO Client (mc) with environment variables."""
    mc_alias = os.environ.get("MC_ALIAS_NAME", "osminio")
    minio_server_url = os.environ.get("MINIO_SERVER_URL")
    minio_access_key = os.environ.get("MINIO_ACCESS_KEY")
    minio_secret_key = os.environ.get("MINIO_SECRET_KEY")
    
    if not all([minio_server_url, minio_access_key, minio_secret_key]):
        print("❌ Error: MinIO environment variables are not set.")
        print("Please set MINIO_SERVER_URL, MINIO_ACCESS_KEY, and MINIO_SECRET_KEY.")
        return False
    
    # Use HTTP for localhost connections, keep original for others
    if "localhost:9000" in minio_server_url:
        if minio_server_url.startswith("https://"):
            minio_server_url = minio_server_url.replace("https://", "http://")
            print(f"🔄 Using HTTP for localhost connection: {minio_server_url}")
    
    try:
        print(f"Configuring MinIO client with alias: {mc_alias}")
        
        # Set up command based on whether we're using localhost
        cmd = [
            "mc", "alias", "set", 
            mc_alias, 
            minio_server_url,
            minio_access_key, 
            minio_secret_key
        ]
        
        # Only add --insecure flag for HTTPS connections
        if not (minio_server_url.startswith("http://") and "localhost" in minio_server_url):
            cmd.append("--insecure")  # Skip TLS certificate verification for non-localhost or HTTPS
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ Error configuring MinIO client: {result.stderr}")
            return False
        
        print("✅ MinIO client configured successfully")
        return True
    except Exception as e:
        print(f"❌ Error configuring MinIO client: {e}")
        return False

def upload_to_minio(file_path):
    """Upload file to MinIO bucket."""
    mc_alias = os.environ.get("MC_ALIAS_NAME", "osminio")
    bucket_name = "pdf-inbox"
    
    try:
        print(f"Uploading {file_path} to MinIO bucket {bucket_name}...")
        cmd = ["mc", "cp", str(file_path), f"{mc_alias}/{bucket_name}/"]
        
        # Get the current MinIO server URL to determine if we need --insecure
        minio_server_url = os.environ.get("MINIO_SERVER_URL", "")
        if not (minio_server_url.startswith("http://") and "localhost" in minio_server_url):
            cmd.append("--insecure")  # Skip TLS certificate verification for non-localhost or HTTPS
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ Error uploading to MinIO: {result.stderr}")
            return False
        
        print(f"✅ File uploaded successfully to {mc_alias}/{bucket_name}/")
        return True
    except Exception as e:
        print(f"❌ Error uploading to MinIO: {e}")
        return False

def main():
    """Main function to generate PDF and upload to MinIO."""
    print("Starting PDF generation and upload process...")
    
    # Create timestamp for filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"event-test-{timestamp}.pdf"
    
    # Create full path for the PDF file
    current_dir = Path.cwd()
    output_path = current_dir / filename
    
    # Generate PDF
    if not generate_pdf(output_path):
        return
    
    # Configure MinIO client
    if not configure_mc():
        return
    
    # Upload PDF to MinIO
    if not upload_to_minio(output_path):
        return
    
    print(f"✅ Process completed successfully. File {filename} uploaded to MinIO pdf-inbox bucket.")
    print(f"This should trigger an event notification in the pipeline.")

if __name__ == "__main__":
    main()

