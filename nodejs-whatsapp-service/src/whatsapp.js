import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("baileys");
import QRCode from "qrcode";
import axios from "axios";
import fs from "fs";
import path from "path";
import config from "./config.js";
import pino from "pino";

// Normalize and stabilize session storage path
const sessionRoot = path.isAbsolute(config.session_path)
  ? config.session_path
  : path.join(process.cwd(), config.session_path);
if (!fs.existsSync(sessionRoot)) {
  fs.mkdirSync(sessionRoot, { recursive: true });
}

function normalizeSessionId(id) {
  return String(id || "default").replace(/[^0-9A-Za-z_\-]/g, "");
}

let sessions = {};
let qrCodes = {};
const starting = new Set(); // prevent concurrent startSession per sid
const backoffUntil = new Map(); // sid -> timestamp ms when next attempt is allowed

export async function getQR(sessionId) {
  const sid = normalizeSessionId(sessionId);
  const until = backoffUntil.get(sid) || 0;
  if (Date.now() < until) {
    return "QR code not available";
  }
  if (qrCodes[sid]) {
    return qrCodes[sid];
  }
  await startSession(sid);
  // Wait briefly for QR event
  for (let i = 0; i < 30; i++) {
    if (qrCodes[sid]) return qrCodes[sid];
    await new Promise((r) => setTimeout(r, 500));
  }
  return "QR code not available";
}

export async function startSession(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (sessions[sid]) {
    return "Session already active";
  }
  if (starting.has(sid)) {
    return "Session starting";
  }
  const until = backoffUntil.get(sid) || 0;
  if (Date.now() < until) {
    return "Backoff in effect";
  }
  try {
    console.log(`Starting session for ${sid}`);
    starting.add(sid);

    if (typeof makeWASocket !== "function") {
      console.error("makeWASocket is not a function. Baileys import failed.");
      throw new Error("makeWASocket is not available");
    }

    const authPath = path.join(sessionRoot, sid);
    const { state, saveCreds } = await useMultiFileAuthState(authPath);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
      version,
      auth: state,
      printQRInTerminal: false,
      browser: ["Chrome", "Linux", "120.0.0"],
      markOnlineOnConnect: false,
      syncFullHistory: false,
      logger: pino({ level: 'info' })
    });

    sock.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;
      console.log(`Connection update for ${sid}:`, {
        connection,
        qr: qr ? "QR received" : "No QR",
      });

      if (qr) {
        try {
          const qrString = await QRCode.toDataURL(qr);
          qrCodes[sid] = qrString;
          console.log(`QR Code generated for session: ${sid}`);
        } catch (qrError) {
          console.error(`QR generation failed for ${sid}:`, qrError);
        }
      }

      if (connection === "close") {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const deviceRemoved = statusCode === 401; // stream:error conflict/device_removed
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        console.log(`Connection closed for ${sid} (status: ${statusCode})`);

        const removed = deviceRemoved || loggedOut;
        try {
          if (removed) {
            if (fs.existsSync(authPath)) {
              fs.rmSync(authPath, { recursive: true, force: true });
              console.log(`Cleared session directory for ${sid}`);
            }
            delete qrCodes[sid];
            // impose backoff to avoid rapid re-pair attempts the phone may reject
            backoffUntil.set(sid, Date.now() + 60_000);
          }
        } catch (e) {
          console.error("Failed clearing session directory:", e);
        }
        // Always drop the in-memory session handle so restart can proceed
        delete sessions[sid];
        if (!removed) {
          setTimeout(() => startSession(sid), 1500);
        } else {
          console.log(
            `Session ${sid} disabled after device removal/log out; waiting for manual restart`
          );
        }
      } else if (connection === "open") {
        console.log(`WhatsApp session ${sid} connected`);
        delete qrCodes[sid];
        backoffUntil.delete(sid);
      }
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("messages.upsert", async (m) => {
      const msg = m.messages[0];
      if (!msg.key.fromMe && msg.message) {
        try {
          const messageText =
            msg.message.conversation ||
            msg.message.extendedTextMessage?.text ||
            "Media message";

          await axios.post(config.erpnext_webhook, {
            session: sid,
            from: msg.key.remoteJid.replace("@s.whatsapp.net", ""),
            text: messageText,
            timestamp: new Date().toISOString(),
          });

          console.log(
            `Forwarded message from ${msg.key.remoteJid} to ERPNext`
          );
        } catch (error) {
          console.error("Failed to forward message to ERPNext:", error.message);
        }
      }
    });

    sessions[sid] = sock;
    console.log(`Session ${sid} started successfully`);
    return "Session started";
  } catch (error) {
    console.error(`Failed to start session ${sessionId}:`, error);
    throw error;
  }
  finally {
    starting.delete(sid);
  }
}

export async function sendMessage(sessionId, to, message) {
  const sid = normalizeSessionId(sessionId);
  const sock = sessions[sid];
  if (!sock) {
    throw new Error("Session not found. Please scan QR code first.");
  }
  try {
    const phoneNumber = to.includes("@") ? to : `${to}@s.whatsapp.net`;
    const result = await sock.sendMessage(phoneNumber, { text: message });
    console.log(`Message sent to ${to}: ${message}`);
    return {
      success: true,
      session: sid,
      messageId: result.key.id,
      to: to,
      message: message,
      timestamp: new Date().toISOString(),
    };
  } catch (error) {
    console.error(`Failed to send message to ${to}:`, error);
    throw new Error(`Failed to send message: ${error.message}`);
  }
}

export function getSessionStatus(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (sessions[sid]) {
    return "Connected";
  } else if (qrCodes[sid]) {
    return "Waiting for scan";
  } else {
    return "Disconnected";
  }
}

export function listSessions() {
  const ids = new Set([...Object.keys(sessions), ...Object.keys(qrCodes)]);
  return Array.from(ids).map((id) => ({ session: id, status: getSessionStatus(id) }));
}

export function resetSession(sessionId) {
  try {
    const sid = normalizeSessionId(sessionId);
    const authPath = path.join(sessionRoot, sid);
    if (fs.existsSync(authPath)) {
      fs.rmSync(authPath, { recursive: true, force: true });
    }
    delete sessions[sid];
    delete qrCodes[sid];
    return { success: true };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}
