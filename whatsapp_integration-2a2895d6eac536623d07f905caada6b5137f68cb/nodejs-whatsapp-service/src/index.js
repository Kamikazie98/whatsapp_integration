import 'dotenv/config';
import express from "express";
import bodyParser from "body-parser";
import { startSession, sendMessage, getQR, getSessionStatus } from "./whatsapp.js";
import config from "./config.js";

const app = express();
app.use(bodyParser.json());

// Health check
app.get("/", (req, res) => {
    res.json({ status: "WhatsApp API Service Running", version: "1.0.0" });
});

// Generate QR & start session
app.get("/qr/:session", async (req, res) => {
    try {
        const qr = await getQR(req.params.session);
        res.json({ qr, session: req.params.session });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// Send message
app.post("/sendMessage", async (req, res) => {
    try {
        const { session, to, message } = req.body;
        const result = await sendMessage(session || "default", to, message);
        res.json(result);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// Get session status
app.get("/status/:session", async (req, res) => {
    try {
        const status = await getSessionStatus(req.params.session);
        res.json({ session: req.params.session, status });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

const PORT = config.port;
app.listen(PORT, () => {
    console.log(`🚀 WhatsApp API Service running on port ${PORT}`);
    console.log(`� Base URL: ${config.base_url}`);
    console.log(`�📱 QR endpoint: ${config.base_url}/qr/default`);
    console.log(`💬 Send message: POST ${config.base_url}/sendMessage`);
    console.log(`📊 Status endpoint: ${config.base_url}/status/default`);
    console.log(`🔄 ERPNext webhook: ${config.erpnext_webhook}`);
});
