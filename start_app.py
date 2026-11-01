"""Start the web app:  python start_app.py   then open http://localhost:8000

  python start_app.py --mock    use the saved correct answers instead of Gemini (no key needed)
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
if "--mock" in sys.argv:
    os.environ["EXTRACTOR"] = "mock"
elif not os.environ.get("GEMINI_API_KEY"):
    print("No GEMINI_API_KEY found, so invoices can only be read from the saved readings.\n"
          "Set the key, or run  python start_app.py --mock  to use the saved correct answers.\n")

import uvicorn  # noqa: E402

# On Windows the default event loop logs a harmless ConnectionResetError every time the
# browser closes a live connection. The "selector" loop doesn't, and works the same here.
LOOP = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    print(f"BillClerk running at http://localhost:{port}  (Ctrl+C to stop, or just close this window)")
    # timeout_graceful_shutdown: on Ctrl+C, don't wait more than a second for an open
    # browser tab to disconnect before stopping
    uvicorn.run("app.server:app", host=host, port=port, log_level="warning", loop=LOOP,
                timeout_graceful_shutdown=1)
