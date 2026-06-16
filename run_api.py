import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.telemetry.main import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
