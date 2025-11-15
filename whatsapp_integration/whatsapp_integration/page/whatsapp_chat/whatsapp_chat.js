frappe.provide("whatsappIntegration");

frappe.pages["whatsapp-chat"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("WhatsApp Chat (Unofficial)"),
		single_column: true,
	});
	wrapper.chat_console = new whatsappIntegration.ChatConsole(page);
};

(function () {
	const PAGE_SIZE = 40;

	class ChatConsole {
		constructor(page) {
			this.page = page;
			this.currentNumber = null;
			this.currentJid = null;
			this.beforeCursor = null;
			this.searchDebounce = null;
			this.isLoadingHistory = false;
			this.whatsappChats = [];
			this.whatsappContacts = [];
			this.currentMessages = [];
			this.sessionCache = {};
			this.wsUrl = null;
			this.ws = null;
			this.wsQueue = [];
			this.wsReconnectTimer = null;
			this.wsReady = false;
			this.pendingSubscription = null;
			this.currentSubscriptionKey = null;
			this.awaitingHistory = false;
			this.historyFallbackTimer = null;
			this.historyFallbackContext = null;
			this.wsSessionId = null;
			this.wsJid = null;

			this.make_layout();
			this.inject_styles();
			this.bind_events();
			this.listen_realtime();
			this.setup_live_bridge();
			this.refresh_devices();
		}

		make_layout() {
			this.page.body.addClass("wa-chat-page");
			this.page.body.html(
				`<div class="wa-chat-container">
					<div class="wa-chat-sidebar">
						<div class="wa-session-picker">
							<label>${__("Device / Session")}</label>
							<select class="form-control wa-session-select">
								<option value="">${__("Auto (connected device)")}</option>
							</select>
						</div>
						<div class="wa-load-actions">
							<button class="btn btn-sm btn-primary wa-load-chats">${__("Load Chats")}</button>
							<button class="btn btn-sm btn-secondary wa-load-contacts">${__("Load Contacts")}</button>
						</div>
						<div class="wa-chat-search">
							<input type="text" class="form-control wa-search-input" placeholder="${__(
								"Search number or name"
							)}">
						</div>
						<div class="wa-chat-list empty-state">
							${__("Click 'Load Chats' or 'Load Contacts' to load from WhatsApp.")}
						</div>
					</div>
					<div class="wa-chat-main">
						<div class="wa-chat-header form-inline">
							<input type="text" class="form-control wa-chat-number" placeholder="${__(
								"Phone number e.g. 98912..."
							)}">
							<button class="btn btn-secondary wa-load-thread">${__("Load")}</button>
							<span class="wa-header-session"></span>
						</div>
						<div class="wa-history-wrapper">
							<button class="btn btn-sm btn-link wa-load-older" disabled>${__("Load older messages")}</button>
							<div class="wa-chat-history">
								<div class="wa-empty">${__("Select a number to view chat.")}</div>
							</div>
						</div>
						<div class="wa-chat-input">
							<textarea class="form-control wa-message-box" rows="3" placeholder="${__(
								"Type message and press Ctrl+Enter to send"
							)}"></textarea>
							<div class="wa-input-actions">
								<button class="btn btn-primary wa-send">${__("Send")}</button>
							</div>
						</div>
					</div>
				</div>`
			);

			this.$sidebar = this.page.body.find(".wa-chat-sidebar");
			this.$chatList = this.page.body.find(".wa-chat-list");
			this.$sessionSelect = this.page.body.find(".wa-session-select");
			this.$numberInput = this.page.body.find(".wa-chat-number");
			this.$history = this.page.body.find(".wa-chat-history");
			this.$messageBox = this.page.body.find(".wa-message-box");
			this.$loadMoreBtn = this.page.body.find(".wa-load-older");
			this.$headerSession = this.page.body.find(".wa-header-session");
		}

		inject_styles() {
			if (document.getElementById("wa-chat-styles")) return;
			const style = document.createElement("style");
			style.id = "wa-chat-styles";
			style.textContent = `
				.wa-chat-container { display:flex; border:1px solid var(--border-color); min-height:60vh; background: var(--card-bg); }
				.wa-chat-sidebar { width:260px; border-right:1px solid var(--border-color); padding:1rem; overflow-y:auto; }
				.wa-chat-main { flex:1; display:flex; flex-direction:column; }
				.wa-chat-header { display:flex; gap:.5rem; padding:1rem; border-bottom:1px solid var(--border-color); }
				.wa-chat-header input { flex:1; }
				.wa-history-wrapper { flex:1; display:flex; flex-direction:column; overflow:hidden; }
				.wa-chat-history { flex:1; overflow-y:auto; padding:1rem; background:#f7f7f7; }
				.wa-chat-input { border-top:1px solid var(--border-color); padding:1rem; }
				.wa-chat-list { margin-top:1rem; }
				.wa-chat-thread { padding:.5rem; border-radius:.4rem; cursor:pointer; border:1px solid transparent; margin-bottom:.5rem; }
				.wa-chat-thread.active { border-color: var(--primary); background:#eef5ff; }
				.wa-chat-thread small { display:block; color:#6c757d; }
				.wa-bubble { max-width:70%; padding:.6rem .8rem; border-radius:.6rem; margin-bottom:.4rem; position:relative; }
				.wa-bubble.in { background:#fff; border:1px solid #e0e0e0; align-self:flex-start; }
				.wa-bubble.out { background:#d1f0d1; align-self:flex-end; }
				.wa-bubble .wa-meta { font-size:.75rem; color:#5f6368; margin-top:.3rem; display:flex; justify-content:space-between; gap:.5rem; }
				.wa-chat-history .wa-row { display:flex; flex-direction:column; }
				.wa-chat-history .wa-row + .wa-row { margin-top:.4rem; }
				.wa-chat-history .wa-empty { text-align:center; color:#888; margin-top:2rem; }
				.wa-chat-search input { width:100%; }
				.wa-load-older { align-self:center; margin:0.4rem 0; }
				.wa-message-box { resize:vertical; min-height:90px; }
				.wa-input-actions { text-align:right; margin-top:.5rem; }
				.wa-load-actions { display:flex; gap:.5rem; margin-bottom:1rem; }
				.wa-load-actions button { flex:1; }
				.wa-chat-thread img { width:40px; height:40px; border-radius:50%; margin-right:.5rem; float:left; }
				.wa-chat-thread .wa-thread-info { overflow:hidden; }
			`;
			document.head.appendChild(style);
		}

		bind_events() {
			this.page.body.find(".wa-load-thread").on("click", () => this.load_thread(true));
			this.$loadMoreBtn.on("click", () => this.load_thread(false));
			
			this.page.body.find(".wa-load-chats").on("click", () => this.load_whatsapp_chats());
			this.page.body.find(".wa-load-contacts").on("click", () => this.load_whatsapp_contacts());

			this.page.body.on("click", ".wa-chat-thread", (e) => {
				const rawJid = e.currentTarget.dataset.jid || "";
				const rawNumber = e.currentTarget.dataset.number || "";
				if (!rawJid && !rawNumber) return;
				const normalizedJid = this.normalize_jid(rawJid || rawNumber);
				const displayNumber = rawNumber || this.jid_to_number(normalizedJid) || rawJid;
				this.currentJid = normalizedJid;
				this.$numberInput.val(displayNumber);
				this.load_thread(true);
			});

			this.$sessionSelect.on("change", () => {
				this.load_whatsapp_chats();
			});

			this.$messageBox.on("keydown", (e) => {
				if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
					e.preventDefault();
					this.send_message();
				}
			});

			this.page.body.find(".wa-send").on("click", () => this.send_message());

			this.page.body.find(".wa-search-input").on("keyup", (e) => {
				clearTimeout(this.searchDebounce);
				this.searchDebounce = setTimeout(() => {
					this.filter_chats(e.currentTarget.value);
				}, 300);
			});
		}

		normalize_jid(value) {
			const raw = (value || "").trim().toLowerCase();
			if (!raw) return null;
			if (raw.includes("@")) return raw;
			const looksLikeGroup = /^\d{6,}-\d{3,}$/.test(raw);
			return `${raw}${looksLikeGroup ? "@g.us" : "@s.whatsapp.net"}`;
		}

		jid_to_number(value) {
			if (!value) return "";
			return value.replace(/@.+$/, "");
		}

		normalize_session_id(value) {
			const raw = (value == null ? "default" : String(value)).trim();
			const cleaned = raw.replace(/[^0-9A-Za-z_\-]/g, "");
			return cleaned || "default";
		}

		listen_realtime() {
			frappe.realtime.on("whatsapp_incoming_message", (payload) => {
				if (!payload || !payload.number) return;
				if (payload.number === this.currentNumber || payload.session === this.$sessionSelect.val()) {
					if (!this.currentSubscriptionKey) {
						this.append_message({
							message: payload.message,
							direction: "In",
							number: payload.number,
							device: payload.device,
							status: "Received",
							sent_time: payload.timestamp,
						});
					}
				}
				// Refresh chats if loaded
				if (this.whatsappChats.length) {
					this.load_whatsapp_chats();
				}
			});

			frappe.realtime.on("whatsapp_chat_update", (payload) => {
				if (!payload || !payload.number) return;
				if (payload.number === this.currentNumber || payload.session === this.$sessionSelect.val()) {
					if (!this.currentSubscriptionKey) {
						this.append_message(payload, payload.direction !== "In");
					}
				}
				// Refresh chats if loaded
				if (this.whatsappChats.length) {
					this.load_whatsapp_chats();
				}
			});
		}

		setup_live_bridge() {
    frappe.call({
        method: "whatsapp_integration.api.chat.get_websocket_url",
        callback: (r) => {
            let raw = r.message && r.message.url;

            // اگر سرور چیزی نداد، دیفالت بگذار
            if (!raw) {
                raw = "/ws/chat";
            }

            // بر اساس پروتکل صفحه ws یا wss انتخاب کن
            const proto = window.location.protocol === "https:" ? "wss" : "ws";
            let finalUrl;

            if (raw.startsWith("ws://") || raw.startsWith("wss://")) {
                // اگر سرور فول URL داد، فقط scheme رو با پروتکل فعلی هماهنگ کن
                try {
                    const u = new URL(raw);
                    finalUrl = `${proto}://${u.host}${u.pathname}${u.search}`;
                } catch (e) {
                    finalUrl = `${proto}://${window.location.host}/ws/chat`;
                }
            } else if (raw.startsWith("/")) {
                // اگر فقط path بود
                finalUrl = `${proto}://${window.location.host}${raw}`;
            } else {
                // هر چیز عجیب → برگردیم به دیفالت امن
                finalUrl = `${proto}://${window.location.host}/ws/chat`;
            }

            this.wsUrl = finalUrl;
			console.log(finalUrl)
            this.connect_websocket();
        },
    });
}


	connect_websocket() {
    if (!this.wsUrl) {
        console.warn("[WA-WS] wsUrl خالی است، کانکت نمی‌شوم");
        return;
    }

    console.log("[WA-WS] تلاش برای اتصال به WebSocket:", this.wsUrl);

    try {
        if (this.ws) {
            console.log("[WA-WS] اتصال قبلی موجود است، بستن آن قبل از اتصال جدید");
            this.ws.close();
        }
        this.ws = new WebSocket(this.wsUrl);
    } catch (err) {
        console.warn("[WA-WS] ساخت WebSocket شکست خورد:", err);
        this.schedule_ws_reconnect();
        return;
    }

    this.ws.onopen = () => {
        console.log("[WA-WS] اتصال باز شد:", this.wsUrl);
        this.wsReady = true;

        const queued = this.wsQueue.splice(0);
        if (queued.length) {
            console.log("[WA-WS] ارسال", queued.length, "پیام در صف بعد از باز شدن اتصال");
        }

        queued.forEach((payload) => {
            try {
                this.ws.send(payload);
            } catch (e) {
                console.warn("[WA-WS] ارسال پیام صف‌شده شکست خورد:", e, "payload:", payload);
            }
        });

        if (this.pendingSubscription) {
            console.log("[WA-WS] ارسال subscribe معوق:", this.pendingSubscription);
            this.send_ws_command({ type: "subscribe", ...this.pendingSubscription });
            this.pendingSubscription = null;
        }
    };

    this.ws.onclose = (event) => {
        console.warn(
            "[WA-WS] اتصال بسته شد. code:",
            event.code,
            "reason:",
            event.reason || "-",
            "wasClean:",
            event.wasClean
        );
        this.wsReady = false;
        this.schedule_ws_reconnect();
    };

    this.ws.onerror = (event) => {
        console.error("[WA-WS] خطای WebSocket:", event);
    };

    this.ws.onmessage = (event) => {
        console.log("[WA-WS] پیام دریافتی از WS:", event.data);
        this.handle_ws_message(event);
    };
}

schedule_ws_reconnect() {
    if (this.wsReconnectTimer) {
        console.log("[WA-WS] تایمر reconnect قبلاً تنظیم شده، انجام نمی‌دهم");
        return;
    }
    if (!this.wsUrl) {
        console.warn("[WA-WS] wsUrl خالی است، reconnect لغو شد");
        return;
    }
    console.log("[WA-WS] زمان‌بندی reconnect برای ۳ ثانیه بعد…");
    this.wsReconnectTimer = setTimeout(() => {
        this.wsReconnectTimer = null;
        console.log("[WA-WS] تلاش برای reconnect به", this.wsUrl);
        this.connect_websocket();
    }, 3000);
}

send_ws_command(payload) {
    if (!this.ws) {
        console.warn("[WA-WS] WebSocket تعریف نشده، نمی‌توانم payload ارسال کنم:", payload);
        return;
    }
    const serialized = JSON.stringify(payload);
    if (this.ws.readyState === WebSocket.OPEN) {
        console.log("[WA-WS] ارسال فوری payload به WS:", payload);
        this.ws.send(serialized);
    } else {
        console.log(
            "[WA-WS] WS هنوز باز نیست (readyState:",
            this.ws.readyState,
            ")، اضافه کردن payload به صف:",
            payload
        );
        this.wsQueue.push(serialized);
    }
}

subscribe_realtime_chat(session, jid, limit = PAGE_SIZE) {
    if (!this.wsUrl) {
        console.warn("[WA-WS] wsUrl خالی است، نمی‌توانم subscribe کنم");
        return;
    }

			const normalizedSession = this.normalize_session_id(session);
			const baseJid = (jid || "").toLowerCase();
			const normalizedJid = baseJid.includes("@") ? baseJid : `${baseJid}@s.whatsapp.net`;
			const key = `${normalizedSession}::${normalizedJid}`;

    console.log("[WA-WS] درخواست subscribe", {
        session,
        jid,
        normalizedSession,
        normalizedJid,
        key,
        currentSubscriptionKey: this.currentSubscriptionKey,
        awaitingHistory: this.awaitingHistory,
    });

    if (this.currentSubscriptionKey === key && !this.awaitingHistory) {
        console.log("[WA-WS] قبلاً روی همین کلید subscribe شده‌ایم، کاری نمی‌کنم");
        return;
    }

    if (this.currentSubscriptionKey && this.currentSubscriptionKey !== key && this.wsSessionId && this.wsJid) {
        console.log("[WA-WS] unsubscribe از subscription قبلی", {
            prevSession: this.wsSessionId,
            prevJid: this.wsJid,
        });
        this.send_ws_command({
            type: "unsubscribe",
            session: this.wsSessionId,
            jid: this.wsJid,
        });
    }

    this.currentSubscriptionKey = key;
    this.wsSessionId = normalizedSession;
    this.wsJid = normalizedJid;
    this.pendingSubscription = { session: normalizedSession, jid: normalizedJid, limit };

    console.log("[WA-WS] ثبت pendingSubscription و ارسال subscribe:", this.pendingSubscription);

    this.send_ws_command({ type: "subscribe", ...this.pendingSubscription });
    this.pendingSubscription = null;
}


		unsubscribe_realtime_chat() {
			if (!this.currentSubscriptionKey || !this.wsSessionId || !this.wsJid) {
				this.currentSubscriptionKey = null;
				return;
			}
			this.send_ws_command({
				type: "unsubscribe",
				session: this.wsSessionId,
				jid: this.wsJid,
			});
			this.currentSubscriptionKey = null;
		}

		handle_ws_message(event) {
			let payload = null;
			try {
				payload = JSON.parse(event.data);
			} catch (err) {
				console.warn("Invalid WebSocket payload", err);
				return;
			}
			if (!payload || !payload.type) return;
			if (payload.type === "history") {
				this.handle_ws_history(payload);
			} else if (payload.type === "message") {
				this.handle_ws_live_message(payload);
			}
		}

		handle_ws_history(payload) {
			const payloadSession = this.normalize_session_id(payload.session || "default");
			const key = `${payloadSession}::${(payload.jid || "").toLowerCase()}`;
			if (key !== this.currentSubscriptionKey) return;
			this.awaitingHistory = false;
			clearTimeout(this.historyFallbackTimer);
			this.historyFallbackContext = null;
			this.isLoadingHistory = false;
			if (!payload.success) {
				this.load_thread_from_db(this.currentNumber, true);
				return;
			}
			const messages = this.map_ws_messages(payload.messages || []);
			if (messages.length) {
				this.beforeCursor = messages[0].sent_time;
			}
			this.render_messages(messages, true);
			this.$loadMoreBtn.prop("disabled", messages.length < PAGE_SIZE);
		}

		handle_ws_live_message(payload) {
			const payloadSession = this.normalize_session_id(payload.session || "default");
			const key = `${payloadSession}::${(payload.jid || "").toLowerCase()}`;
			if (key !== this.currentSubscriptionKey) return;
			const entries = this.map_ws_messages([payload.message]);
			if (entries.length) {
				this.append_message(entries[0]);
			}
		}

		map_ws_messages(messages) {
			return (messages || [])
				.map((msg) => {
					if (!msg) return null;
					return {
						number: msg.from,
						message: msg.message,
						direction: msg.fromMe ? "Out" : "In",
						status: msg.status || (msg.fromMe ? "Sent" : "Received"),
						sent_time: msg.timestamp,
						device: null,
					};
				})
				.filter(Boolean);
		}

		resolve_node_session(session) {
			const key = session || "__auto__";
			if (this.sessionCache[key]) {
				return Promise.resolve(this.sessionCache[key]);
			}
			return new Promise((resolve, reject) => {
				frappe.call({
					method: "whatsapp_integration.api.chat.resolve_node_session",
					args: { session },
					callback: (r) => {
						const resolved = (r.message && r.message.session) || "default";
						this.sessionCache[key] = resolved;
						resolve(resolved);
					},
					error: (err) => reject(err),
				});
			});
		}

		load_thread_via_ws(number, jid, session, resetCursor) {
			const targetNumber = number || this.currentNumber;
			this.start_history_fallback(targetNumber, resetCursor);
			this.resolve_node_session(session)
				.then((resolvedSession) => {
					this.subscribe_realtime_chat(resolvedSession, jid, PAGE_SIZE);
				})
				.catch(() => {
					this.awaitingHistory = false;
					clearTimeout(this.historyFallbackTimer);
					this.load_thread_from_db(targetNumber, resetCursor);
				});
		}

		start_history_fallback(number, resetCursor) {
			clearTimeout(this.historyFallbackTimer);
			this.historyFallbackContext = { number, resetCursor };
			this.awaitingHistory = true;
			this.historyFallbackTimer = setTimeout(() => {
				this.awaitingHistory = false;
				const ctx = this.historyFallbackContext;
				if (ctx) {
					this.load_thread_from_db(ctx.number, ctx.resetCursor);
				}
			}, 4000);
		}

		refresh_devices() {
			frappe.call({
				method: "whatsapp_integration.api.chat.get_available_devices",
				callback: (r) => {
					const devices = (r.message && r.message.devices) || [];
					const $select = this.$sessionSelect;
					$select.find("option:not(:first)").remove();
					devices.forEach((device) => {
						const label = `${device.name} (${device.status || __("Unknown")})`;
						$select.append(
							$("<option>", {
								value: device.name,
								text: label,
							})
						);
					});
				},
			});
		}

		load_recent_numbers(search) {
			frappe.call({
				method: "whatsapp_integration.api.chat.list_recent_numbers",
				args: { search },
				callback: (r) => {
					const numbers = (r.message && r.message.numbers) || [];
					if (!numbers.length) {
						this.$chatList
							.addClass("empty-state")
							.html(`<div class="text-muted small">${__("No conversations yet.")}</div>`);
						return;
					}
					this.$chatList.removeClass("empty-state").empty();
					numbers.forEach((row) => {
						const number = frappe.utils.escape_html(row.number || "");
						const label = frappe.utils.escape_html(
							row.last_time ? frappe.datetime.str_to_user(row.last_time) : __("No activity")
						);
						const thread = $(`
							<div class="wa-chat-thread" data-number="${number}">
								<strong>${number}</strong>
								<small>${label}</small>
							</div>
						`);
						if (number === this.currentNumber) {
							thread.addClass("active");
						}
						this.$chatList.append(thread);
					});
				},
			});
		}

		load_whatsapp_chats() {
			const session = this.$sessionSelect.val() || null;
			frappe.call({
				method: "whatsapp_integration.api.chat.load_whatsapp_chats",
				args: { session },
				freeze: true,
				freeze_message: __("Loading chats from WhatsApp..."),
				callback: (r) => {
					if (r.message && r.message.success && r.message.chats) {
						this.whatsappChats = r.message.chats;
						this.render_chats_list();
					} else {
						frappe.msgprint(__("Failed to load chats from WhatsApp."));
					}
				},
				error: () => {
					frappe.msgprint(__("Error loading chats. Check Node service and device status."));
				},
			});
		}

		load_whatsapp_contacts() {
			const session = this.$sessionSelect.val() || null;
			frappe.call({
				method: "whatsapp_integration.api.chat.load_whatsapp_contacts",
				args: { session },
				freeze: true,
				freeze_message: __("Loading contacts from WhatsApp..."),
				callback: (r) => {
					if (r.message && r.message.success && r.message.contacts) {
						this.whatsappContacts = r.message.contacts;
						this.render_contacts_list();
					} else {
						frappe.msgprint(__("Failed to load contacts from WhatsApp."));
					}
				},
				error: () => {
					frappe.msgprint(__("Error loading contacts. Check Node service and device status."));
				},
			});
		}

		render_chats_list() {
			if (!this.whatsappChats.length) {
				this.$chatList
					.addClass("empty-state")
					.html(`<div class="text-muted small">${__("No chats found.")}</div>`);
				return;
			}
			this.$chatList.removeClass("empty-state").empty();
			this.whatsappChats.forEach((chat) => {
				const name = frappe.utils.escape_html(chat.name || chat.number || "");
				const number = frappe.utils.escape_html(chat.number || "");
				const jid = chat.id || chat.number;
				const isGroup = chat.isGroup || false;
				const profilePic = chat.profilePicture || "";
				
				const thread = $(`
					<div class="wa-chat-thread" data-jid="${jid}" data-number="${number}">
						${profilePic ? `<img src="${profilePic}" alt="${name}" onerror="this.style.display='none'">` : ""}
						<div class="wa-thread-info">
							<strong>${name}</strong>
							<small>${number} ${isGroup ? __("(Group)") : ""}</small>
						</div>
					</div>
				`);
				if (jid === this.currentJid || number === this.currentNumber) {
					thread.addClass("active");
				}
				this.$chatList.append(thread);
			});
		}

		render_contacts_list() {
			if (!this.whatsappContacts.length) {
				this.$chatList
					.addClass("empty-state")
					.html(`<div class="text-muted small">${__("No contacts found.")}</div>`);
				return;
			}
			this.$chatList.removeClass("empty-state").empty();
			this.whatsappContacts.forEach((contact) => {
				const name = frappe.utils.escape_html(contact.name || contact.number || "");
				const number = frappe.utils.escape_html(contact.number || "");
				const jid = contact.id || contact.number;
				const profilePic = contact.profilePicture || "";
				
				const thread = $(`
					<div class="wa-chat-thread" data-jid="${jid}" data-number="${number}">
						${profilePic ? `<img src="${profilePic}" alt="${name}" onerror="this.style.display='none'">` : ""}
						<div class="wa-thread-info">
							<strong>${name}</strong>
							<small>${number}</small>
						</div>
					</div>
				`);
				if (jid === this.currentJid || number === this.currentNumber) {
					thread.addClass("active");
				}
				this.$chatList.append(thread);
			});
		}

		filter_chats(search) {
			if (!search) {
				if (this.whatsappChats.length) {
					this.render_chats_list();
				} else if (this.whatsappContacts.length) {
					this.render_contacts_list();
				} else {
					this.load_recent_numbers();
				}
				return;
			}
			
			const searchLower = search.toLowerCase();
			const filtered = [];
			
			if (this.whatsappChats.length) {
				filtered.push(...this.whatsappChats.filter(
					(chat) => 
						(chat.name && chat.name.toLowerCase().includes(searchLower)) ||
						(chat.number && chat.number.includes(search))
				));
			} else if (this.whatsappContacts.length) {
				filtered.push(...this.whatsappContacts.filter(
					(contact) => 
						(contact.name && contact.name.toLowerCase().includes(searchLower)) ||
						(contact.number && contact.number.includes(search))
				));
			}
			
			if (filtered.length) {
				this.$chatList.removeClass("empty-state").empty();
				filtered.forEach((item) => {
					const name = frappe.utils.escape_html(item.name || item.number || "");
					const number = frappe.utils.escape_html(item.number || "");
					const jid = item.id || item.number;
					const isGroup = item.isGroup || false;
					const profilePic = item.profilePicture || "";
					
					const thread = $(`
						<div class="wa-chat-thread" data-jid="${jid}" data-number="${number}">
							${profilePic ? `<img src="${profilePic}" alt="${name}" onerror="this.style.display='none'">` : ""}
							<div class="wa-thread-info">
								<strong>${name}</strong>
								<small>${number} ${isGroup ? __("(Group)") : ""}</small>
							</div>
						</div>
					`);
					if (jid === this.currentJid || number === this.currentNumber) {
						thread.addClass("active");
					}
					this.$chatList.append(thread);
				});
			} else {
				this.$chatList
					.addClass("empty-state")
					.html(`<div class="text-muted small">${__("No matches found.")}</div>`);
			}
		}

		load_thread(resetCursor) {
			const inputValue = (this.$numberInput.val() || "").trim();
			const inputLooksLikeJid = inputValue.includes("@");
			const normalizedInputJid = this.normalize_jid(inputValue);
			const numberFromInput = inputLooksLikeJid ? this.jid_to_number(inputValue) : inputValue;

			let jid = this.currentJid;
			if (resetCursor) {
				if (inputValue) {
					if (inputLooksLikeJid) {
						jid = normalizedInputJid;
					} else if (this.currentJid && this.jid_to_number(this.currentJid) === numberFromInput) {
						jid = this.currentJid;
					} else {
						jid = normalizedInputJid;
					}
				}
			} else if (!jid) {
				jid = normalizedInputJid;
			}

			let number = resetCursor ? numberFromInput : this.currentNumber || numberFromInput;
			if (resetCursor && !number && this.currentNumber) {
				number = this.currentNumber;
			}
			if (!number && jid) {
				number = this.jid_to_number(jid);
			}

			if (!jid && !number) {
				frappe.msgprint(__("Enter a phone number or select a chat first."));
				return;
			}
			if (this.isLoadingHistory) return;
			clearTimeout(this.historyFallbackTimer);
			this.historyFallbackContext = null;
			this.awaitingHistory = false;

			if (resetCursor) {
				this.beforeCursor = null;
				this.$history.empty();
				this.currentNumber = number;
				this.currentJid = jid;
				this.currentMessages = [];
				this.$numberInput.val(this.currentNumber || this.jid_to_number(jid) || "");
				this.$chatList.find(".wa-chat-thread").removeClass("active");
				this.$chatList
					.find(".wa-chat-thread")
					.filter((_, el) => el.dataset.jid === jid || el.dataset.number === number)
					.addClass("active");
				this.unsubscribe_realtime_chat();
			}

			this.isLoadingHistory = true;

			const session = this.$sessionSelect.val() || null;
			this.$headerSession.text(this.$sessionSelect.find("option:selected").text() || __("Auto"));

			const canUseRealtime = resetCursor && this.wsUrl && jid && jid.includes("@");
			if (canUseRealtime) {
				this.load_thread_via_ws(number, jid, session, resetCursor);
				return;
			}

			// Try loading from Node service via server if we have JID
			if (jid && jid.includes("@")) {
				frappe.call({
					method: "whatsapp_integration.api.chat.load_whatsapp_messages",
					args: { session, jid, limit: PAGE_SIZE },
					callback: (r) => {
						if (r.message && r.message.success && r.message.messages) {
							const messages = r.message.messages.map((msg) => ({
								number: msg.from,
								message: msg.message,
								direction: msg.fromMe ? "Out" : "In",
								status: msg.status || (msg.fromMe ? "Sent" : "Received"),
								sent_time: msg.timestamp,
								device: null,
							}));
							this.render_messages(messages, resetCursor);
							this.$loadMoreBtn.prop("disabled", messages.length < PAGE_SIZE);
						} else {
							this.load_thread_from_db(number, resetCursor);
						}
					},
					always: () => {
						this.isLoadingHistory = false;
					},
				});
				return;
			}

			// Load from database
			this.load_thread_from_db(number, resetCursor);
		}

		load_thread_from_db(number, resetCursor) {
			const args = { number, limit: PAGE_SIZE };
			if (!resetCursor && this.beforeCursor) {
				args.before = this.beforeCursor;
			}

			frappe.call({
				method: "whatsapp_integration.api.chat.get_chat_history",
				args,
				callback: (r) => {
					const messages = (r.message && r.message.messages) || [];
					if (messages.length) {
						this.beforeCursor = messages[0].sent_time;
					}
					this.render_messages(messages, resetCursor);
					this.$loadMoreBtn.prop("disabled", messages.length < PAGE_SIZE);
					this.$headerSession.text(
						this.$sessionSelect.find("option:selected").text() || __("Auto")
					);
				},
				always: () => {
					this.isLoadingHistory = false;
				},
			});
		}

		render_messages(messages, reset) {
			if (reset && !messages.length) {
				this.$history.html(`<div class="wa-empty">${__("No messages yet.")}</div>`);
				return;
			}
			if (reset) {
				this.$history.empty();
			}

			this.isRenderingBatch = true;
			if (reset) {
				messages.forEach((msg) => this.append_message(msg, false, false));
			} else {
				for (let idx = messages.length - 1; idx >= 0; idx--) {
					this.append_message(messages[idx], false, true);
				}
			}
			this.isRenderingBatch = false;

			if (reset) {
				this.currentMessages = messages.slice();
			} else {
				this.currentMessages = [...messages, ...(this.currentMessages || [])];
			}

			if (reset) {
				this.scroll_to_bottom();
			}
		}

		append_message(msg, scroll = true, prepend = false) {
			const container = $("<div>", { class: "wa-row" });
			const bubble = $("<div>", { class: `wa-bubble ${msg.direction === "In" ? "in" : "out"}` });
			const text = frappe.utils.escape_html(msg.message || "");
			bubble.append(`<div class="wa-text">${text.replace(/\n/g, "<br>")}</div>`);

			const meta = $("<div>", { class: "wa-meta" });
			const status =
				msg.direction === "Out"
					? (msg.status || __("Sending"))
					: __("Incoming");
			const timeLabel = msg.sent_time ? frappe.datetime.str_to_user(msg.sent_time) : __("Now");
			meta.append(`<span>${status}</span>`);
			meta.append(`<span>${timeLabel}</span>`);

			if (msg.error_message && msg.direction === "Out") {
				meta.append(`<span class="text-danger">${frappe.utils.escape_html(msg.error_message)}</span>`);
			}

			bubble.append(meta);
			container.append(bubble);

			if (prepend && this.$history.children().length) {
				this.$history.prepend(container);
			} else {
				this.$history.append(container);
			}

			if (!this.isRenderingBatch) {
				if (!Array.isArray(this.currentMessages)) {
					this.currentMessages = [];
				}
				if (prepend) {
					this.currentMessages.unshift(msg);
				} else {
					this.currentMessages.push(msg);
				}
			}

			if (scroll) this.scroll_to_bottom();
		}

		scroll_to_bottom() {
			this.$history.stop().animate({ scrollTop: this.$history[0].scrollHeight }, 300);
		}

		send_message() {
			const number = (this.$numberInput.val() || "").trim();
			const message = (this.$messageBox.val() || "").trim();
			if (!number || !message) {
				frappe.msgprint(__("Number and message are required."));
				return;
			}
			const session = this.$sessionSelect.val() || null;

			frappe.call({
				method: "whatsapp_integration.api.chat.send_chat_message",
				args: { number, message, session },
				freeze: true,
				freeze_message: __("Sending WhatsApp message..."),
				callback: (r) => {
					const log = r.message && r.message.log;
					if (log) {
						this.append_message(log);
					}
					this.$messageBox.val("");
				},
				error: () => {
					frappe.msgprint(__("Failed to send message. Check Node service and device status."));
				},
			});
		}
	}

	whatsappIntegration.ChatConsole = ChatConsole;
})();
