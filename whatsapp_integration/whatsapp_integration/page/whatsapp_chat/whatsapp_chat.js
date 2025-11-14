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
			this.beforeCursor = null;
			this.searchDebounce = null;
			this.isLoadingHistory = false;

			this.make_layout();
			this.inject_styles();
			this.bind_events();
			this.listen_realtime();
			this.refresh_devices();
			this.load_recent_numbers();
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
						<div class="wa-chat-search">
							<input type="text" class="form-control wa-search-input" placeholder="${__(
								"Search number"
							)}">
						</div>
						<div class="wa-chat-list empty-state">
							${__("No conversations yet.")}
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
			`;
			document.head.appendChild(style);
		}

		bind_events() {
			this.page.body.find(".wa-load-thread").on("click", () => this.load_thread(true));
			this.$loadMoreBtn.on("click", () => this.load_thread(false));

			this.page.body.on("click", ".wa-chat-thread", (e) => {
				const number = e.currentTarget.dataset.number;
				if (!number) return;
				this.$numberInput.val(number);
				this.load_thread(true);
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
					this.load_recent_numbers(e.currentTarget.value);
				}, 300);
			});
		}

		listen_realtime() {
			frappe.realtime.on("whatsapp_incoming_message", (payload) => {
				if (!payload || !payload.number) return;
				if (payload.number === this.currentNumber) {
					this.append_message({
						message: payload.message,
						direction: "In",
						number: payload.number,
						device: payload.device,
						status: "Received",
						sent_time: payload.timestamp,
					});
				}
				this.load_recent_numbers();
			});

			frappe.realtime.on("whatsapp_chat_update", (payload) => {
				if (!payload || !payload.number) return;
				if (payload.number === this.currentNumber) {
					this.append_message(payload, payload.direction !== "In");
				}
				this.load_recent_numbers();
			});
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

		load_thread(resetCursor) {
			const number = (this.$numberInput.val() || "").trim();
			if (!number) {
				frappe.msgprint(__("Enter a phone number first."));
				return;
			}
			if (this.isLoadingHistory) return;

			this.isLoadingHistory = true;
			if (resetCursor) {
				this.beforeCursor = null;
				this.$history.empty();
				this.currentNumber = number;
				this.$chatList.find(".wa-chat-thread").removeClass("active");
				this.$chatList
					.find(".wa-chat-thread")
					.filter((_, el) => el.dataset.number === number)
					.addClass("active");
			}

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

			if (reset) {
				messages.forEach((msg) => this.append_message(msg, false, false));
			} else {
				for (let idx = messages.length - 1; idx >= 0; idx--) {
					this.append_message(messages[idx], false, true);
				}
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
