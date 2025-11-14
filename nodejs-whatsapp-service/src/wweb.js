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
    } catch (e) {
      console.error('Failed to render QR', e);
    }
  });

  client.on('ready', () => {
    ready.add(sid);
    qrCodes.delete(sid);
    console.log(`WWebJS session ${sid} ready`);
  });

  client.on('disconnected', async (reason) => {
    console.warn(`WWebJS session ${sid} disconnected: ${reason}`);
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
  if (!client || !ready.has(sid)) throw new Error('Session not found. Please scan QR code first.');
  const jid = to.includes('@') ? to : `${to}@c.us`;
  const res = await client.sendMessage(jid, message);
  return { success: true, session: sid, messageId: res.id.id, to, message, timestamp: new Date().toISOString() };
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

