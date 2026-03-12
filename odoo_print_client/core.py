import base64
import json
import subprocess
import requests
import websocket

def process_print_job(payload_dict):
    """
    Validates the payload and pipes the PDF bytes directly from memory 
    into the CUPS spooler via stdin, ensuring zero footprint on disk.
    """
    print("\n--- New print job received ---")
    printer_name = payload_dict.get("printer_name", "")
    file_data_b64 = payload_dict.get("file_data", "")

    if not file_data_b64:
        print("[!] Error: Incomplete payload. Missing file data.")
        return

    try:
        pdf_bytes = base64.b64decode(file_data_b64, validate=True)
    except Exception:
        print("[!] Security Error: Content is not valid Base64.")
        return

    if not pdf_bytes.startswith(b"%PDF"):
        print("[!] SECURITY ALERT: The sent file is NOT a real PDF. Aborting.")
        return

    print(f"[*] Sending directly from RAM to printer: {printer_name if printer_name else 'Default'}...")
    
    cmd = ["lp"]
    if printer_name:
        cmd.extend(["-d", printer_name])

    try:
        print(f"[*] Executing command: {' '.join(cmd)}")
        subprocess.run(cmd, input=pdf_bytes, check=True)
        print("[*] Print job successfully queued in CUPS! (Zero disk footprint)")
    except subprocess.CalledProcessError as e:
        print(f"[!] CUPS Error during printing: {e}")
    except FileNotFoundError:
        print("[!] Error: 'lp' command not found. Is CUPS installed?")
    except Exception as e:
        print(f"[!] Unexpected error during printing: {e}")


def run_client(url, db, user, password, channel):
    """
    Authenticates with Odoo and starts the WebSocket listening loop.
    """
    auth_payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"db": db, "login": user, "password": password}
    }

    print(f"[*] Authenticating with Odoo at {url} (DB: {db})...")
    try:
        with requests.Session() as session:
            resp = session.post(f"{url}/web/session/authenticate", json=auth_payload)
            resp.raise_for_status()
            session_id = session.cookies.get("session_id")
    except Exception as e:
        print(f"[!] Authentication error: {e}")
        return

    if not session_id:
        print("[!] Error: Could not obtain session_id. Check credentials.")
        return

    ws_url = url.replace("http", "ws").replace("https", "wss") + "/websocket"
    print(f"[*] Connecting to WebSocket at {ws_url}...")

    try:
        socket = websocket.create_connection(ws_url, cookie=f"session_id={session_id}")
    except Exception as e:
        print(f"[!] Failed to connect to WebSocket: {e}")
        return

    socket.send(json.dumps({
        'event_name': 'subscribe',
        'data': {'channels': [channel], 'last': 0}
    }))

    print("[*] Connected and listening on 'printer' channel...")

    while True:
        try:
            raw_message = socket.recv()
            data = json.loads(raw_message)

            if isinstance(data, list):
                for event in data:
                    bus_message = event.get("message", {})
                    if isinstance(bus_message, dict) and bus_message.get("type") == "print_job":
                        real_payload = bus_message.get("payload", {})
                        if "file_data" in real_payload:
                            process_print_job(real_payload)

        except json.JSONDecodeError:
            pass 
        except Exception as e:
            print(f"[!] Connection closed or unexpected error: {e}")
            break
