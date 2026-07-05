import time
import os

LOG_FILE = "D:\\Projects\\patchitRIGHT\\mcp_debug.log"

def log_step(message: str):
    try:
        # Clear log if it grows too large
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1024 * 1024:
            try:
                os.remove(LOG_FILE)
            except Exception:
                pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass
