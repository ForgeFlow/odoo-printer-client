import os
import argparse
from dotenv import load_dotenv
from .core import run_client

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Odoo Local Printer WebSocket Client")
    
    parser.add_argument("--url", default=os.getenv("ODOO_URL"), help="Odoo Base URL")
    parser.add_argument("--db", default=os.getenv("ODOO_DB"), help="Odoo Database Name")
    parser.add_argument("--user", default=os.getenv("ODOO_USER"), help="Odoo Username")
    parser.add_argument("--password", default=os.getenv("ODOO_PASSWORD"), help="Odoo Password")

    args = parser.parse_args()

    # Safety check: Ensure all variables are present
    if not all([args.url, args.db, args.user, args.password]):
        print("[!] Error: Missing credentials.")
        print("[*] Please provide them via CLI arguments or set them in a .env file.")
        return

    run_client(args.url, args.db, args.user, args.password)

if __name__ == "__main__":
    main()