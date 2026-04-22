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
    print("Open your browser to: http://localhost:8000/settings")
    print("="*60)
    
    # Run the FastAPI app
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )