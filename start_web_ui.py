#!/usr/bin/env python3
"""
Start the TalkBot Web UI Server
Runs the FastAPI application for web-based bot control
"""

import sys
import os
import uvicorn
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Load environment variables
load_dotenv()

if __name__ == "__main__":
    print("="*60)
    print("Starting TalkBot Web UI Server")
    print("="*60)
    # Loopback by default: the dashboard has no login. API_HOST opts in to
    # exposing it on the network.
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    print(f"Open your browser to: http://localhost:{port}/settings")
    print("="*60)
    
    # Run the FastAPI app
    uvicorn.run(
        "src.api.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )