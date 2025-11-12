import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require("@whiskeysockets/baileys");
import QRCode from "qrcode";
import axios from "axios";
import config from "./config.js";

let sessions = {};
let qrCodes = {};

export async function getQR(sessionId) {
    if (qrCodes[sessionId]) {
        return qrCodes[sessionId];
    }
    
    await startSession(sessionId);
    return qrCodes[sessionId] || "QR code not available";
}

export async function startSession(sessionId) {
    if (sessions[sessionId]) {
        return "Session already active";
    }

    try {
        console.log(`📱 Starting session for ${sessionId}`);
        
        // Check if makeWASocket is available
        if (typeof makeWASocket !== 'function') {
            console.error('❌ makeWASocket is not a function. Baileys import failed.');
            throw new Error('makeWASocket is not available');
        }

        const { state, saveCreds } = await useMultiFileAuthState(`./sessions/${sessionId}`);
        
        const sock = makeWASocket({ 
            auth: state,
            printQRInTerminal: true
        });

        sock.ev.on("connection.update", async (update) => {
            const { connection, lastDisconnect, qr } = update;
            
            console.log(`📡 Connection update for ${sessionId}:`, { connection, qr: qr ? 'QR received' : 'No QR' });
            
            if (qr) {
                try {
                    const qrString = await QRCode.toDataURL(qr);
                    qrCodes[sessionId] = qrString;
                    console.log(`✅ QR Code generated for session: ${sessionId}`);
                } catch (qrError) {
                    console.error(`❌ QR generation failed for ${sessionId}:`, qrError);
                }
            }
            
            if (connection === "close") {
                const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
                console.log(`🔌 Connection closed for ${sessionId}:`, lastDisconnect.error, ", reconnecting:", shouldReconnect);
                
                if (shouldReconnect) {
                    startSession(sessionId);
                }
            } else if (connection === "open") {
                console.log(`✅ WhatsApp session ${sessionId} connected`);
                delete qrCodes[sessionId]; // Clear QR after connection
            }
        });

        sock.ev.on("creds.update", saveCreds);
        
        sock.ev.on("messages.upsert", async (m) => {
            const msg = m.messages[0];
            if (!msg.key.fromMe && msg.message) {
                // Forward incoming message to ERPNext webhook
                try {
                    const messageText = msg.message.conversation || 
                                     msg.message.extendedTextMessage?.text || 
                                     "Media message";
                    
                    await axios.post(config.erpnext_webhook, {
                        session: sessionId,
                        from: msg.key.remoteJid.replace("@s.whatsapp.net", ""),
                        text: messageText,
                        timestamp: new Date().toISOString()
                    });
                    
                    console.log(`📨 Forwarded message from ${msg.key.remoteJid} to ERPNext`);
                } catch (error) {
                    console.error("Failed to forward message to ERPNext:", error.message);
                }
            }
        });

        sessions[sessionId] = sock;
        console.log(`🚀 Session ${sessionId} started successfully`);
        return "Session started";
    } catch (error) {
        console.error(`❌ Failed to start session ${sessionId}:`, error);
        throw error;
    }
}

export async function sendMessage(sessionId, to, message) {
    const sock = sessions[sessionId];
    if (!sock) {
        throw new Error("Session not found. Please scan QR code first.");
    }

    try {
        // Ensure phone number format
        const phoneNumber = to.includes("@") ? to : `${to}@s.whatsapp.net`;
        
        const result = await sock.sendMessage(phoneNumber, { text: message });
        
        console.log(`✅ Message sent to ${to}: ${message}`);
        return { 
            success: true, 
            messageId: result.key.id,
            to: to,
            message: message,
            timestamp: new Date().toISOString()
        };
    } catch (error) {
        console.error(`❌ Failed to send message to ${to}:`, error);
        throw new Error(`Failed to send message: ${error.message}`);
    }
}

export function getSessionStatus(sessionId) {
    if (sessions[sessionId]) {
        return "Connected";
    } else if (qrCodes[sessionId]) {
        return "Waiting for scan";
    } else {
        return "Disconnected";
    }
}
