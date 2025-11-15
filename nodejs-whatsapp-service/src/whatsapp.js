import { createRequire } from "module";
const require = createRequire(import.meta.url);
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("baileys");
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

const defaultQueryTimeoutMs = Number(process.env.BAILEYS_QUERY_TIMEOUT_MS || 90000);

function normalizeSessionId(id) {
  return String(id || "default").replace(/[^0-9A-Za-z_\-]/g, "");
}

let sessions = {};
let qrCodes = {};
const readySessions = new Set(); // sessions with established WhatsApp connection
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
      defaultQueryTimeoutMs,
      logger: pino({ level: "info" }),
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

        readySessions.delete(sid);

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
        readySessions.add(sid);
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
  if (!readySessions.has(sid)) {
    throw new Error("Session is not connected yet. Please wait for the device to come online.");
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
    const timedOut =
      /timed\s*out/i.test(error?.message || "") ||
      error?.output?.statusCode === 408 ||
      error?.data?.statusCode === 408;
    if (timedOut) {
      readySessions.delete(sid);
      if (sessions[sid]) {
        try {
          sessions[sid].end?.(new Error("Restarting after send timeout"));
        } catch (e) {
          console.warn("Failed ending socket cleanly:", e);
        }
        delete sessions[sid];
      }
      setTimeout(() => startSession(sid).catch((reconnectErr) => {
        console.error(`Auto-reconnect failed for ${sid}:`, reconnectErr);
      }), 2000);
      throw new Error(
        "WhatsApp session timed out while sending. Node service is reconnecting; please retry in a few seconds."
      );
    }
    throw new Error(`Failed to send message: ${error.message || String(error)}`);
  }
}

export function getSessionStatus(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (readySessions.has(sid)) {
    return "Connected";
  }
  if (qrCodes[sid]) {
    return "Waiting for scan";
  }
  if (sessions[sid]) {
    return "Connecting";
  }
  return "Disconnected";
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
    readySessions.delete(sid);
    return { success: true };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export async function getChats(sessionId) {
  const sid = normalizeSessionId(sessionId);
  const sock = sessions[sid];
  if (!sock) {
    throw new Error("Session not found. Please scan QR code first.");
  }
  if (!readySessions.has(sid)) {
    throw new Error("Session is not connected yet.");
  }
  
  try {
    const chatList = [];
    
    // Get all chats from WhatsApp
    try {
      // Fetch all groups first
      const groups = await sock.groupFetchAllParticipating().catch(() => ({ groups: [] }));
      const groupMap = new Map();
      if (groups && groups.groups) {
        for (const [jid, group] of Object.entries(groups.groups)) {
          groupMap.set(jid, {
            id: jid,
            number: jid.replace('@g.us', ''),
            name: group.subject || jid.replace('@g.us', ''),
            isGroup: true,
            profilePicture: null,
          });
        }
      }
      
      // Get individual chats - Baileys doesn't have a direct method for this
      // We'll use the message history approach by storing recent chats
      // For now, return groups and a note that individual chats require message history
      
      const allChats = Array.from(groupMap.values());
      
      return { success: true, chats: allChats, note: "Individual chats will be populated from message history" };
    } catch (err) {
      console.error(`Error fetching chats:`, err.message);
      // Return empty list on error
      return { success: true, chats: [] };
    }
  } catch (error) {
    console.error(`Failed to get chats for ${sid}:`, error);
    throw new Error(`Failed to get chats: ${error.message || String(error)}`);
  }
}

export async function getContacts(sessionId) {
  const sid = normalizeSessionId(sessionId);
  const sock = sessions[sid];
  if (!sock) {
    throw new Error("Session not found. Please scan QR code first.");
  }
  if (!readySessions.has(sid)) {
    throw new Error("Session is not connected yet.");
  }
  
  try {
    const contacts = [];
    
    // Baileys doesn't have a direct contacts list API
    // We need to get contacts from phone book or message history
    // For now, we'll return contacts from recent message senders/receivers
    // This is a limitation - WhatsApp Web doesn't expose full contact list
    
    // Try to get business profile if available
    try {
      // Note: Baileys doesn't have getBusinessProfile - this is a placeholder
      // Contacts need to be extracted from message history or stored separately
      console.log(`Note: WhatsApp Web doesn't expose full contact list. Use message history instead.`);
    } catch (err) {
      console.error(`Error:`, err.message);
    }
    
    return { 
      success: true, 
      contacts: contacts,
      note: "WhatsApp Web doesn't expose full contact list. Contacts will be populated from message history."
    };
  } catch (error) {
    console.error(`Failed to get contacts for ${sid}:`, error);
    throw new Error(`Failed to get contacts: ${error.message || String(error)}`);
  }
}

export async function getChatMessages(sessionId, jid, limit = 50) {
  const sid = normalizeSessionId(sessionId);
  const sock = sessions[sid];
  if (!sock) {
    throw new Error("Session not found. Please scan QR code first.");
  }
  if (!readySessions.has(sid)) {
    throw new Error("Session is not connected yet.");
  }
  
  try {
    const normalizedJid = jid.includes('@') ? jid : `${jid}@s.whatsapp.net`;
    
    // Baileys uses fetchMessagesFromWA for getting message history
    // Note: This may not work for all cases - WhatsApp Web limits message history access
    let messages = [];
    try {
      messages = await sock.fetchMessagesFromWA(normalizedJid, limit);
    } catch (err) {
      console.warn(`fetchMessagesFromWA failed for ${normalizedJid}:`, err.message);
      // Return empty messages - suggest using database history instead
      return { 
        success: true, 
        messages: [],
        note: "Unable to fetch messages from WhatsApp. Use database message history instead."
      };
    }
    
    const formattedMessages = messages.map((msg) => {
      const remoteJid = msg.key.remoteJid || normalizedJid;
      const from = remoteJid.includes('@g.us') 
        ? (msg.key.participant?.replace('@s.whatsapp.net', '') || remoteJid.replace('@g.us', ''))
        : remoteJid.replace('@s.whatsapp.net', '');
      
      return {
        id: msg.key.id,
        from: from,
        fromMe: msg.key.fromMe || false,
        message: msg.message?.conversation || 
                 msg.message?.extendedTextMessage?.text || 
                 msg.message?.imageMessage?.caption ||
                 msg.message?.videoMessage?.caption ||
                 'Media message',
        timestamp: msg.messageTimestamp 
          ? new Date(msg.messageTimestamp * 1000).toISOString()
          : new Date().toISOString(),
        status: msg.status || (msg.key.fromMe ? 'sent' : 'received'),
      };
    });
    
    return { success: true, messages: formattedMessages };
  } catch (error) {
    console.error(`Failed to get messages for ${sid}/${jid}:`, error);
    throw new Error(`Failed to get messages: ${error.message || String(error)}`);
  }
}
