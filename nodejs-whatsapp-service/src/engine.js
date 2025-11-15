import config from './config.js';

// Lazy conditional export to keep identical API surface
let impl;
if ((config.engine || 'baileys').toLowerCase() === 'wwebjs') {
  impl = await import('./wweb.js');
} else {
  impl = await import('./whatsapp.js');
}

export const getQR = impl.getQR;
export const startSession = impl.startSession;
export const sendMessage = impl.sendMessage;
export const getSessionStatus = impl.getSessionStatus;
export const listSessions = impl.listSessions ?? (() => []);
export const resetSession = impl.resetSession ?? (() => ({ success: false, error: 'not_supported' }));
export const getChats = impl.getChats ?? (() => Promise.reject(new Error('not_supported')));
export const getContacts = impl.getContacts ?? (() => Promise.reject(new Error('not_supported')));
export const getChatMessages = impl.getChatMessages ?? (() => Promise.reject(new Error('not_supported')));

