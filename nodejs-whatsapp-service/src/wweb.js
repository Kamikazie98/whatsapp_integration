import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { Client, LocalAuth } = require('whatsapp-web.js');
import QRCode from 'qrcode';
import axios from 'axios';
import fs from 'fs';
import path from 'path';
import config from './config.js';

const sessions = new Map(); // sid -> Client
const qrCodes = new Map(); // sid -> dataURL
const ready = new Set(); // sid
let wss;

export function setWebSocketServer(wsServer) {
  wss = wsServer;
  wss.on('connection', ws => {
    console.log('WebSocket client connected');
    ws.on('message', message => {
      console.log('received: %s', message);
    });
  });
}

const sessionRoot = path.isAbsolute(config.session_path)
  ? config.session_path
  : path.join(process.cwd(), config.session_path);
if (!fs.existsSync(sessionRoot)) fs.mkdirSync(sessionRoot, { recursive: true });

function normalizeSessionId(id) {
  return String(id || 'default').replace(/[^0-9A-Za-z_\-]/g, '');
}

export async function getQR(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (qrCodes.has(sid)) return qrCodes.get(sid);
  await startSession(sid);
  for (let i = 0; i < 30; i++) {
    if (qrCodes.has(sid)) return qrCodes.get(sid);
    await new Promise(r => setTimeout(r, 500));
  }
  return 'QR code not available';
}

export async function startSession(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (sessions.has(sid)) return 'Session already active';

  const dataPath = path.join(sessionRoot, 'wwebjs');
  const client = new Client({
    authStrategy: new LocalAuth({ clientId: sid, dataPath }),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    },
    takeoverOnConflict: false,
    takeoverTimeoutMs: 0,
  });

  client.on('qr', async (qr) => {
    try {
      const dataUrl = await QRCode.toDataURL(qr);
      qrCodes.set(sid, dataUrl);
      console.log(`WWebJS QR generated for ${sid}`);
      if (wss) {
        wss.clients.forEach(client => {
          if (client.readyState === 1) { // 1 = WebSocket.OPEN
            client.send(JSON.stringify({ type: 'qr', session: sid, qr: dataUrl }));
          }
        });
      }
    } catch (e) {
      console.error('Failed to render QR', e);
    }
  });

  client.on('ready', () => {
    ready.add(sid);
    qrCodes.delete(sid);
    console.log(`[${sid}] WWebJS session ready.`);
    if (wss) {
        wss.clients.forEach(client => {
            if (client.readyState === 1) { // 1 = WebSocket.OPEN
                client.send(JSON.stringify({ type: 'status', session: sid, status: 'connected' }));
            }
        });
    }
  });

  client.on('disconnected', async (reason) => {
    console.warn(`[${sid}] WWebJS session disconnected: ${reason}`);
    ready.delete(sid);
    sessions.delete(sid);
    // Do not auto-delete auth; allow manual reset via API
  });

  client.on('message', async (msg) => {
    try {
      const text = msg.body || 'Media message';
      await axios.post(config.erpnext_webhook, {
        session: sid,
        from: msg.from.replace('@c.us', '').replace('@s.whatsapp.net',''),
        text,
        timestamp: new Date().toISOString(),
      });
      console.log(`WWebJS forwarded message from ${msg.from}`);
    } catch (e) {
      console.error('Failed to forward message to ERPNext:', e?.message || e);
    }
  });

  sessions.set(sid, client);
  await client.initialize();
  return 'Session started';
}

export async function sendMessage(sessionId, to, message) {
  const sid = normalizeSessionId(sessionId);
  const client = sessions.get(sid);
  if (!client || !ready.has(sid)) {
    console.error(`[${sid}] Session not found or not ready for sendMessage.`);
    throw new Error('Session not found. Please scan QR code first.');
  }
  try {
    const jid = to.includes('@') ? to : `${to}@c.us`;
    console.log(`[${sid}] Sending message to ${jid}`);
    const res = await client.sendMessage(jid, message);
    console.log(`[${sid}] Message sent successfully to ${to}. Message ID: ${res.id.id}`);
    return { success: true, messageId: res.id.id, to, message, timestamp: new Date().toISOString() };
  } catch (error) {
    console.error(`[${sid}] Failed to send message to ${to}:`, error);
    throw new Error(`Failed to send message: ${error.message}`);
  }
}

export function getSessionStatus(sessionId) {
  const sid = normalizeSessionId(sessionId);
  if (ready.has(sid)) return 'Connected';
  if (qrCodes.has(sid)) return 'Waiting for scan';
  return 'Disconnected';
}

export function listSessions() {
  return Array.from(new Set([...sessions.keys(), ...qrCodes.keys()]))
    .map((sid) => ({ session: sid, status: getSessionStatus(sid) }));
}

export function resetSession(sessionId) {
  const sid = normalizeSessionId(sessionId);
  try {
    const dataPath = path.join(sessionRoot, 'wwebjs');
    // LocalAuth stores in dataPath/.wwebjs_auth/session-<clientId>
    const authDir = path.join(dataPath, '.wwebjs_auth', `session-${sid}`);
    if (fs.existsSync(authDir)) {
      fs.rmSync(authDir, { recursive: true, force: true });
    }
    sessions.delete(sid);
    ready.delete(sid);
    qrCodes.delete(sid);
    return { success: true };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

