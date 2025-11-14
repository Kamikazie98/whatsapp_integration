import config from './config.js';

// Lazy conditional export to keep identical API surface
let impl;
if ((config.engine || 'baileys').toLowerCase() === 'wwebjs') {
  impl = await import('./wweb.js');
} else {
  impl = await import('./whatsapp.js');
}

export function setWebSocketServer(wss) {
  if (impl.setWebSocketServer) {
    impl.setWebSocketServer(wss);
  }
}

export const getQR = impl.getQR;
export const startSession = impl.startSession;
export const sendMessage = impl.sendMessage;
export const getSessionStatus = impl.getSessionStatus;
export const listSessions = impl.listSessions ?? (() => []);
export const resetSession = impl.resetSession ?? (() => ({ success: false, error: 'not_supported' }));

