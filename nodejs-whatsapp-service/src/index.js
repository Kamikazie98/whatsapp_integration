import 'dotenv/config';
import * as nodeCrypto from 'crypto';
if (!globalThis.crypto || !('hkdfSync' in globalThis.crypto)) {
  // @ts-ignore
  globalThis.crypto = nodeCrypto;
}
import express from 'express';
import bodyParser from 'body-parser';
import { startSession, sendMessage, getQR, getSessionStatus, listSessions, resetSession } from './engine.js';
import config from './config.js';

const app = express();
app.use(bodyParser.json());

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

const PORT = config.port;
app.listen(PORT, () => {
  console.log(`WhatsApp API Service running on port ${PORT}`);
  console.log(`Base URL: ${config.base_url}`);
  console.log(`QR endpoint: ${config.base_url}/qr/default`);
  console.log(`Send message: POST ${config.base_url}/sendMessage`);
  console.log(`Status endpoint: ${config.base_url}/status/default`);
  console.log(`Sessions: ${config.base_url}/sessions`);
  console.log(`Reset: POST ${config.base_url}/reset`);
  console.log(`ERPNext webhook: ${config.erpnext_webhook}`);
});

