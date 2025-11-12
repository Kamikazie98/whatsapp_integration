export default {
    port: process.env.WHATSAPP_API_PORT || 8001,
    frappe_host: process.env.FRAPPE_HOST || "localhost",
    frappe_port: process.env.FRAPPE_PORT || 8002,
    get erpnext_webhook() {
        return `http://${this.frappe_host}:${this.frappe_port}/api/method/whatsapp_integration.api.webhook.receive_message`;
    },
    get base_url() {
        return `http://${this.frappe_host}:${this.port}`;
    },
    session_path: "./sessions"
};
