import 'dotenv/config';
import * as nodeCrypto from 'crypto';

const cryptoImpl =
  globalThis.crypto ??
  nodeCrypto.webcrypto ??
  nodeCrypto;

if (!globalThis.crypto) {
  // Older Node versions expose no global crypto; safe to assign
  // eslint-disable-next-line no-global-assign
  globalThis.crypto = cryptoImpl;
} else if (!('hkdfSync' in globalThis.crypto) && typeof nodeCrypto.hkdfSync === 'function') {
  // Node 20+ exposes read-only global crypto; augment it with hkdfSync if missing
  Object.defineProperty(globalThis.crypto, 'hkdfSync', {
    value: nodeCrypto.hkdfSync.bind(nodeCrypto),
    configurable: true,
  });
}

import express from 'express';
import bodyParser from 'body-parser';
import { WebSocketServer, WebSocket } from 'ws';
import { startSession, sendMessage, getQR, getSessionStatus, listSessions, resetSession, getChats, getContacts, getChatMessages, onRealtimeMessage } from './engine.js';
import config from './config.js';

const app = express();
app.use(bodyParser.json());

function normalizeSessionId(id) {
  return String(id || 'default').replace(/[^0-9A-Za-z_\-]/g, '');
}

function normalizeChatJid(jid) {
  if (!jid) return null;
  const trimmed = String(jid).trim().toLowerCase();
  if (!trimmed) return null;
  return trimmed.includes('@') ? trimmed : `${trimmed}@s.whatsapp.net`;
}

function sendJson(ws, payload) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

const wsClients = new Set();

app.get('/', (_req, res) => {
  res.json({ status: 'WhatsApp API Service Running', version: '1.0.0' });
});

app.get('/qr/:session', async (req, res) => {
  try {
    const qr = await getQR(req.params.session);
    res.json({ qr, session: req.params.session });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/sendMessage', async (req, res) => {
  try {
    const { session, to, message } = req.body;
    const result = await sendMessage(session || 'default', to, message);
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/status/:session', async (req, res) => {
  try {
    const status = await getSessionStatus(req.params.session);
    res.json({ session: req.params.session, status });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/reset', (req, res) => {
  try {
    const { session } = req.body;
    if (!session) return res.status(400).json({ error: 'session is required' });
    res.json(resetSession(session));
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/sessions', (_req, res) => {
  try {
    res.json({ sessions: listSessions() });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/chats/:session', async (req, res) => {
  try {
    const result = await getChats(req.params.session);
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/contacts/:session', async (req, res) => {
  try {
    const result = await getContacts(req.params.session);
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/messages/:session/:jid', async (req, res) => {
  try {
    const { session, jid } = req.params;
    const limit = parseInt(req.query.limit) || 50;
    const result = await getChatMessages(session, jid, limit);
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

const PORT = config.port;
const server = app.listen(PORT, () => {
  console.log(`WhatsApp API Service running on port ${PORT}`);
  console.log(`Base URL: ${config.base_url}`);
  console.log(`QR endpoint: ${config.base_url}/qr/default`);
  console.log(`Send message: POST ${config.base_url}/sendMessage`);
  console.log(`Status endpoint: ${config.base_url}/status/default`);
  console.log(`Sessions: ${config.base_url}/sessions`);
  console.log(`Reset: POST ${config.base_url}/reset`);
  console.log(`ERPNext webhook: ${config.erpnext_webhook}`);
});

const wss = new WebSocketServer({ server, path: '/ws/chat' });

wss.on('connection', (socket) => {
  socket.subscriptions = new Set();
  wsClients.add(socket);

  socket.on('message', async (raw) => {
    let payload;
    try {
      payload = JSON.parse(raw.toString());
    } catch (err) {
      return sendJson(socket, { type: 'error', error: 'invalid_json', detail: err.message });
    }

    switch (payload?.type) {
      case 'subscribe': {
        const session = normalizeSessionId(payload.session);
        const jid = normalizeChatJid(payload.jid);
        if (!jid) {
          sendJson(socket, { type: 'error', error: 'invalid_jid' });
          break;
        }
        const key = `${session}::${jid}`;
        socket.subscriptions.add(key);
        sendJson(socket, { type: 'subscribed', session, jid });
        try {
          const history = await getChatMessages(session, jid, payload.limit || 50);
          sendJson(socket, {
            type: 'history',
            session,
            jid,
            success: history.success !== false,
            messages: history.messages || [],
            note: history.note,
          });
        } catch (err) {
          sendJson(socket, {
            type: 'history',
            session,
            jid,
            success: false,
            error: err.message || String(err),
          });
        }
        break;
      }
      case 'unsubscribe': {
        const session = normalizeSessionId(payload.session);
        const jid = normalizeChatJid(payload.jid);
        if (!jid) {
          sendJson(socket, { type: 'error', error: 'invalid_jid' });
          break;
        }
        const key = `${session}::${jid}`;
        socket.subscriptions.delete(key);
        sendJson(socket, { type: 'unsubscribed', session, jid });
        break;
      }
      case 'ping': {
        sendJson(socket, { type: 'pong', ts: Date.now() });
        break;
      }
      default:
        sendJson(socket, { type: 'error', error: 'unknown_command' });
    }
  });

  const cleanup = () => {
    socket.subscriptions.clear();
    wsClients.delete(socket);
  };

  socket.on('close', cleanup);
  socket.on('error', cleanup);
});

onRealtimeMessage((payload) => {
  if (!payload?.session || !payload?.jid || !payload?.message) {
    return;
  }
  const key = `${payload.session}::${payload.jid}`;
  const data = JSON.stringify({ type: "message", ...payload });
  for (const client of wsClients) {
    if (client.readyState !== WebSocket.OPEN) continue;
    if (!client.subscriptions?.has(key)) continue;
    client.send(data);
  }
});

