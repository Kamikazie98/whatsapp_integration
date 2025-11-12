# WhatsApp Integration for ERPNext

A complete WhatsApp messaging and campaign management solution for ERPNext/Frappe, supporting both Official WhatsApp Business API and Unofficial WhatsApp Web integration.

## Features
- Dual mode: Official (Meta Cloud API) and Unofficial (WhatsApp Web)
- Bulk messaging, campaign management, automation, and more

## Installation

### 1. Install App in Frappe/ERPNext

```bash
cd /path/to/your/bench
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site <yoursite> install-app whatsapp_integration
```

### 2. Install Python Dependencies

From the app directory:

```bash
cd apps/whatsapp_integration
pip install -r requirements.txt
```

### 3. Install Node.js Service Dependencies

From the Node.js service directory:

```bash
cd nodejs-whatsapp-service
npm install
```

### 4. Start Node.js WhatsApp Service

```bash
cd nodejs-whatsapp-service
npm start
```

Or, for development with auto-reload:

```bash
npm run dev
```

### 5. Start Frappe/ERPNext Bench

```bash
cd /path/to/your/bench
bench start
```

## Usage
- Go to the WhatsApp Device DocType in ERPNext
- Click "Generate QR Code" and scan with WhatsApp mobile app
- Follow on-screen instructions for linking and troubleshooting

## Troubleshooting
- If you see "Can't link new devices at this time", wait 2-5 minutes and try again
- Remove old linked devices from WhatsApp mobile app
- Always generate a fresh QR code for each attempt

## Requirements

### Python
- Python 3.10+
- Frappe/ERPNext 15+
- See `requirements.txt` for all Python dependencies

### Node.js
- Node.js 18+
- See `nodejs-whatsapp-service/package.json` for all Node.js dependencies

## Contributing
This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/whatsapp_integration
pre-commit install
```

## License
MIT

---
For more details, see the full documentation and troubleshooting guides in this repository.
