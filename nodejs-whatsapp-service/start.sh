#!/bin/bash

# WhatsApp API Service Startup Script
# Automatically detects Frappe server port and starts the service

echo "🔍 Detecting Frappe server configuration..."

# Try to find running Frappe process and extract port
FRAPPE_PORT=$(ps aux | grep "frappe serve --port" | grep -v grep | sed -n 's/.*--port \([0-9]*\).*/\1/p' | head -1)

if [ -z "$FRAPPE_PORT" ]; then
    echo "⚠️  Could not detect Frappe server port, using default 8002"
    FRAPPE_PORT=8002
else
    echo "✅ Detected Frappe server running on port $FRAPPE_PORT"
fi

# Calculate WhatsApp API port (Frappe port - 1)
WHATSAPP_API_PORT=$((FRAPPE_PORT - 1))

echo "🚀 Starting WhatsApp API Service on port $WHATSAPP_API_PORT"

# Export environment variables
export FRAPPE_PORT=$FRAPPE_PORT
export WHATSAPP_API_PORT=$WHATSAPP_API_PORT
export FRAPPE_HOST=${FRAPPE_HOST:-localhost}

# Start the service
npm start
