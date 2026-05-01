#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/asher/project/agent-world/src')
from agent_world.db import init_db
init_db()
print("DB ready")

import asyncio
import websockets
from http.server import HTTPServer
from pathlib import Path
import threading

PORT = 8765
WS_PORT = 8766

from web.world_viewer import WorldRequestHandler, start_ws_server

def main():
    print(f"Starting HTTP server on 0.0.0.0:{PORT}")
    handler = WorldRequestHandler
    server = HTTPServer(("0.0.0.0", PORT), handler)
    
    def serve():
        server.serve_forever()
    
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    
    print(f"🌐 HTTP Server: http://0.0.0.0:{PORT}")
    print(f"   API: http://0.0.0.0:{PORT}/api/world")
    print(f"   WebSocket: ws://0.0.0.0:{WS_PORT}")
    print()
    print("Server running... Press Ctrl+C to stop")
    
    try:
        asyncio.run(start_ws_server())
    except KeyboardInterrupt:
        print("\nServer stopped")
        server.shutdown()

if __name__ == "__main__":
    main()
