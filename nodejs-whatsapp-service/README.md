# WhatsApp Integration - NodeJS Service

This is the unofficial WhatsApp API service that connects to WhatsApp Web using Baileys library.

## Setup

1. Install dependencies:
```bash
cd nodejs-whatsapp-service
npm install
```

2. Configure environment (optional):
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. Start the service:
```bash
npm start
```

## Configuration

Set these environment variables or create a `.env` file:
- `WHATSAPP_API_PORT`: Service port (default: 3001)
- `FRAPPE_HOST`: Frappe server hostname (default: localhost)
- `FRAPPE_PORT`: Frappe server port (default: 8002)
- `SESSION_PATH`: Path to store session data (default: ./sessions)

## Usage

1. **Generate QR Code**: GET `/qr/default`
2. **Send Message**: POST `/sendMessage`
   ```json
   {
     "session": "default",
     "to": "1234567890",
     "message": "Hello from ERPNext!"
   }
   ```
3. **Check Status**: GET `/status/default`

## Integration with ERPNext

1. In ERPNext WhatsApp Settings, set Mode = "Unofficial"
2. Set NodeJS API URL = "http://localhost:3001" (or your configured port)
3. The service will automatically forward incoming messages to ERPNext

## Production Deployment

For production, use PM2 or similar process manager:
```bash
pm2 start src/index.js --name "whatsapp-api"
pm2 save
pm2 startup
```
