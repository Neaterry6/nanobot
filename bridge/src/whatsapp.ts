/**
 * WhatsApp client wrapper using Baileys number pairing (no QR flow).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  Browsers,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import pino from 'pino';
import { mkdir, readFile, rm, writeFile } from 'fs/promises';
import { join } from 'path';

const VERSION = '0.1.0';
const PAIRING_CODE_FILE = 'pairing.json';
const PAIRING_META_FILE = 'pairing-meta.json';

export interface InboundMessage {
  id: string;
  sender: string;
  pn: string;
  content: string;
  timestamp: number;
  isGroup: boolean;
}

export interface PairingCodePayload {
  number: string;
  code: string;
  sessionPath: string;
  timestamp: string;
}

export interface WhatsAppClientOptions {
  authDir: string;
  phoneNumber?: string;
  onMessage: (msg: InboundMessage) => void;
  onPairingCode: (payload: PairingCodePayload) => void;
  onStatus: (status: string) => void;
}

function normalizeNumber(value = ''): string | null {
  const clean = String(value || '').replace(/\D/g, '');
  if (clean.length < 10 || clean.length > 15) return null;
  return clean;
}

function formatCode(code = ''): string {
  return code?.match(/.{1,4}/g)?.join('-') || code;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readRegistered(authDir: string): Promise<boolean> {
  try {
    const raw = await readFile(join(authDir, 'creds.json'), 'utf8');
    return JSON.parse(raw)?.registered === true;
  } catch {
    return false;
  }
}

async function requestPairingCodeWithRetry(sock: any, number: string, retries = 3): Promise<string> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const rawCode = await sock.requestPairingCode(number);
      if (!rawCode) throw new Error('Pairing API returned an empty code');
      return rawCode;
    } catch (error) {
      lastError = error;
      if (attempt < retries) await sleep(1200 * attempt);
    }
  }
  throw lastError || new Error('Could not generate pairing code');
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private reconnecting = false;
  private pairingCodeIssued = false;

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  async connect(): Promise<void> {
    const logger = pino({ level: 'silent' });
    await mkdir(this.options.authDir, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1025091844] as [number, number, number] }));

    console.log(`Using Baileys version: ${version.join('.')}`);

    const browser = typeof Browsers?.ubuntu === 'function'
      ? Browsers.ubuntu('Chrome')
      : ['broken', 'cli', VERSION] as [string, string, string];

    this.sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser,
      syncFullHistory: false,
      markOnlineOnConnect: false,
      defaultQueryTimeoutMs: 60000,
      connectTimeoutMs: 60000,
      keepAliveIntervalMs: 25000,
      retryRequestDelayMs: 250,
      generateHighQualityLinkPreview: false,
      getMessage: async () => ({ conversation: '' }),
    });

    this.sock.ev.on('creds.update', saveCreds);
    this.attachSocketHandlers();
    await this.maybeRequestPairingCode();
  }

  private async maybeRequestPairingCode(): Promise<void> {
    if (this.pairingCodeIssued || await readRegistered(this.options.authDir)) return;

    const number = normalizeNumber(this.options.phoneNumber || process.env.WHATSAPP_PHONE_NUMBER || '');
    if (!number) {
      const message = 'WhatsApp number pairing requires WHATSAPP_PHONE_NUMBER (10-15 digits with country code). QR pairing is disabled.';
      console.error(message);
      this.options.onStatus('pairing_number_required');
      return;
    }

    this.pairingCodeIssued = true;
    await sleep(700);
    const rawCode = await requestPairingCodeWithRetry(this.sock, number, 3);
    const code = formatCode(rawCode);
    const payload = {
      number,
      code,
      sessionPath: this.options.authDir,
      timestamp: new Date().toISOString(),
    };

    await writeFile(join(this.options.authDir, PAIRING_META_FILE), JSON.stringify({ number, createdAt: payload.timestamp }, null, 2));
    await writeFile(join(this.options.authDir, PAIRING_CODE_FILE), JSON.stringify(payload, null, 2));
    console.log(`🔐 WhatsApp pairing code for ${number}: ${code}`);
    this.options.onPairingCode(payload);
  }

  private attachSocketHandlers(): void {
    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('Ignoring WhatsApp QR update because broken uses number pairing only.');
        this.options.onStatus('qr_ignored_number_pairing_only');
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(`Connection closed. Status: ${statusCode}, Will reconnect: ${shouldReconnect}`);
        this.options.onStatus('disconnected');

        if (!shouldReconnect) {
          await rm(this.options.authDir, { recursive: true, force: true }).catch(() => undefined);
          return;
        }

        if (shouldReconnect && !this.reconnecting) {
          this.reconnecting = true;
          console.log('Reconnecting in 5 seconds...');
          setTimeout(() => {
            this.reconnecting = false;
            this.connect();
          }, 5000);
        }
      } else if (connection === 'open') {
        console.log('✅ Connected to WhatsApp');
        this.options.onStatus('connected');
      }
    });

    this.sock.ev.on('messages.upsert', async ({ messages, type }: { messages: any[]; type: string }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        if (msg.key.fromMe) continue;
        if (msg.key.remoteJid === 'status@broadcast') continue;

        const content = this.extractMessageContent(msg);
        if (!content) continue;

        const isGroup = msg.key.remoteJid?.endsWith('@g.us') || false;
        this.options.onMessage({
          id: msg.key.id || '',
          sender: msg.key.remoteJid || '',
          pn: msg.key.remoteJidAlt || '',
          content,
          timestamp: msg.messageTimestamp as number,
          isGroup,
        });
      }
    });
  }

  private extractMessageContent(msg: any): string | null {
    const message = msg.message;
    if (!message) return null;
    if (message.conversation) return message.conversation;
    if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
    if (message.imageMessage?.caption) return `[Image] ${message.imageMessage.caption}`;
    if (message.videoMessage?.caption) return `[Video] ${message.videoMessage.caption}`;
    if (message.documentMessage?.caption) return `[Document] ${message.documentMessage.caption}`;
    if (message.audioMessage) return `[Voice Message]`;
    return null;
  }

  async sendMessage(to: string, text: string): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    await this.sock.sendMessage(to, { text });
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
  }
}
