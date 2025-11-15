import fs from "fs";
import path from "path";

const engine = process.env.WHATSAPP_ENGINE || "baileys"; // "baileys" | "wwebjs"
const sessionPath = process.env.WA_SESSIONS_DIR || "./sessions";

function resolveFirstNumber(values) {
  for (const value of values) {
    const parsed = parseInt(value, 10);
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed;
    }
  }
  return null;
}

function detectFrappePort() {
  const envPort = resolveFirstNumber([
    process.env.FRAPPE_PORT,
    process.env.FRAPPE_SERVER_PORT,
    process.env.BENCH_PORT,
  ]);
  if (envPort) {
    return envPort;
  }

  const candidates = [
    path.resolve(process.cwd(), "..", "sites", "common_site_config.json"),
    path.resolve(process.cwd(), "..", "..", "sites", "common_site_config.json"),
    path.resolve(process.cwd(), "..", "..", "..", "sites", "common_site_config.json"),
    process.env.FRAPPE_BENCH_PATH
      ? path.resolve(process.env.FRAPPE_BENCH_PATH, "sites", "common_site_config.json")
      : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (!fs.existsSync(candidate)) {
      continue;
    }
    try {
      const config = JSON.parse(fs.readFileSync(candidate, "utf8"));
      const detected = resolveFirstNumber([config.webserver_port, config.socketio_port]);
      if (detected) {
        return detected;
      }
    } catch (err) {
      console.warn(`Failed reading ${candidate}: ${err.message}`);
    }
  }
  return 8000;
}

const frappePort = detectFrappePort();
const frappeHost = process.env.FRAPPE_HOST || "127.0.0.1";
const serviceHost = process.env.WHATSAPP_SERVICE_HOST || process.env.WHATSAPP_API_HOST || "127.0.0.1";
const apiPort = parseInt(process.env.WHATSAPP_API_PORT, 10) || 8001;

export default {
  port: apiPort,
  frappe_host: frappeHost,
  frappe_port: frappePort,
  engine,
  get erpnext_webhook() {
    return `http://${this.frappe_host}:${this.frappe_port}/api/method/whatsapp_integration.api.webhook.receive_message`;
  },
  get base_url() {
    return `http://${serviceHost}:${this.port}`;
  },
  session_path: sessionPath,
};
