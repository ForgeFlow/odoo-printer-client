import logging
import os
import argparse
from dotenv import load_dotenv
from .core import run_client, DEFAULT_PING_INTERVAL

def main():
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Odoo Local Printer WebSocket Client")
    
    parser.add_argument("--url", default=os.getenv("ODOO_URL"), help="Odoo Base URL")
    parser.add_argument("--url-ssl-noverify", action="store_true", help="Odoo Base URL - Don't verify SSL certificate")
    parser.add_argument("--db", default=os.getenv("ODOO_DB"), help="Odoo Database Name")
    parser.add_argument("--user", default=os.getenv("ODOO_USER"), help="Odoo Username")
    parser.add_argument("--password", default=os.getenv("ODOO_PASSWORD"), help="Odoo Password")
    parser.add_argument("--channel", default=os.getenv("ODOO_CHANNEL"), help="WebSocket Channel to listen to")
    parser.add_argument(
        "--ping-interval",
        type=int,
        default=int(os.getenv("ODOO_PING_INTERVAL", DEFAULT_PING_INTERVAL)),
        help="Seconds of inactivity before sending a WebSocket keepalive ping",
    )

    args = parser.parse_args()

    # Safety check: Ensure all variables are present
    if not all([args.url, args.db, args.user, args.password, args.channel]):
        print("[!] Error: Missing credentials.")
        print("[*] Please provide them via CLI arguments or set them in a .env file.")
        return

    ssl_verify = not args.url_ssl_noverify
    run_client(
        args.url, args.db, args.user, args.password, args.channel,
        ssl_verify=ssl_verify,
        ping_interval=args.ping_interval,
    )

if __name__ == "__main__":
    main()