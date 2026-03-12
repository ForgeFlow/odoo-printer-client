# Odoo Local Print Client

A secure, zero-footprint local printing client for Odoo. This client connects to your Odoo instance via WebSockets, listens for print jobs on the Odoo Bus, and pipes the PDF documents directly to your local Linux print spooler (CUPS) without ever saving the files to disk.

## Features

* **Zero Footprint (In-Memory Printing):** Documents are decoded in RAM and piped directly to the standard input (`stdin`) of the OS print command. No temporary files are created, ensuring maximum data privacy (GDPR compliant).
* **High Security:**
  * Validates the file's Magic Bytes to ensure the payload is a genuine PDF (`%PDF`).
  * Uses strict Base64 decoding.
  * Prevents OS Command Injection by executing subprocesses safely without `shell=True`.
* **Firewall Friendly:** Uses WebSockets to initiate an outbound connection to Odoo. No inbound ports need to be opened on the client's local network.
* **Environment Variables (.env):** Securely manage Odoo credentials without hardcoding them or passing them via command-line arguments.

## Prerequisites

* **Linux OS** (Ubuntu, Debian, etc.)
* **CUPS** (Common UNIX Printing System) installed and running. The `lp` command must be available in the system path.
* **Python 3.7+**

## Installation

1. Clone or copy this repository to the target machine.
2. Navigate to the project directory:
   ```bash
   cd odoo-print-client
   ```
3. (Optional but recommended) Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
4. Install the package using pip in editable mode:
   ```bash
   pip install -e .
   ```

## Configuration

For security reasons, do not pass passwords via the command line. Instead, create a `.env` file in the directory from which you will run the client.

1. Create the `.env` file:
   ```
   ODOO_URL=http://localhost:8069
   ODOO_DB=your_database_name
   ODOO_USER=print_service_user
   ODOO_PASSWORD=your_super_secure_password
   ```
2. **Crucial Security Step:** Secure the file so only the owner can read it:
   ```bash
   chmod 600 .env
   ```

> **Note:** It is highly recommended to create a dedicated Odoo user (e.g., `print_service`) with minimal access rights solely for authenticating this client, rather than using the admin account.

## Usage

Once installed and configured, simply run the CLI command:

```bash
odoo-printer
```

The client will automatically load the credentials from the `.env` file, authenticate via JSON-RPC, establish the WebSocket connection, and listen for incoming print jobs.

### Overriding configurations

If you need to test a different environment or override the `.env` variables, you can pass arguments directly:

```bash
odoo-printer --url "https://odoo.example.com" --db "prod" --user "admin" --password "admin"
```

## How it works (Odoo Side)

This client expects a specific JSON payload sent through the Odoo `bus.bus` on the printer channel. You will need a custom Odoo module to intercept the print action and send the payload.

Expected payload structure sent to the bus:

```json
{
  "printer_name": "Name_of_CUPS_Printer",
  "file_data": "JVBERi0xLjQKJcOkw7zDts..."
}
```

Where `file_data` is the strictly Base64 encoded byte string of the PDF.
