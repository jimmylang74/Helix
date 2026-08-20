const ilinkbot = {
    _es: null,
    _pollTimer: null,

    async init() {
        const result = await apiCall('imbot.wechat.status');
        if (result.success) this._updateUI(result.status);
        this._connectSSE();
    },

    async requestQR() {
        const btn = document.getElementById('ilinkbotQrBtn');
        btn.disabled = true;
        btn.querySelector('span').textContent = __('config.ilinkbot.requesting');

        console.log('[iBot] Requesting QR code...');
        const result = await apiCall('imbot.wechat.qrcode');
        console.log('[iBot] QR code response:', JSON.stringify(result));
        btn.disabled = false;
        btn.querySelector('span').textContent = __('config.ilinkbot.scanLogin');

        if (!result.success || !result.qrcode_img_content) {
            console.error('[iBot] QR code failed:', result.error || 'no qrcode_img_content');
            showToast(__('config.ilinkbot.qrFailed') + (result.error || ''), 'error');
            return;
        }

        document.getElementById('ilinkbotQrImg').src = result.qrcode_img_base64;
        document.getElementById('ilinkbotQrArea').style.display = 'block';
        document.getElementById('ilinkbotQrStatus').textContent = __('config.ilinkbot.waitingScan');

        this._pollQRStatus(result.qrcode_id);
    },

    _pollQRStatus(qrcode) {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            const result = await apiCall('imbot.wechat.qrcode_status', { qrcode });
            if (!result.success || result.error) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
                return;
            }

            const status = result.status;
            const statusEl = document.getElementById('ilinkbotQrStatus');

            if (result.authenticated) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
                statusEl.textContent = __('config.ilinkbot.authSuccess');
                statusEl.style.color = 'green';
                document.getElementById('ilinkbotStartBtn').style.display = '';
                document.getElementById('ilinkbotLogoutBtn').style.display = '';
                this._updateStatusBadge(true, true);
                showToast(__('config.ilinkbot.authSuccess'), 'success');
            } else if (status === 'expired') {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
                statusEl.textContent = __('config.ilinkbot.qrExpired');
                statusEl.style.color = 'red';
            } else if (status === 'scanned') {
                statusEl.textContent = __('config.ilinkbot.scanned');
            }
        }, 3000);
    },

    async start() {
        const timeout = parseInt(document.getElementById('ilinkbotPollTimeout').value) || 50;
        const result = await apiCall('imbot.wechat.start', { poll_timeout: timeout });
        if (result.success) {
            this._updateUI(result.status);
            showToast(__('config.ilinkbot.started'), 'success');
        } else {
            showToast(result.error || __('config.ilinkbot.startFailed'), 'error');
        }
    },

    async stop() {
        const result = await apiCall('imbot.wechat.stop');
        if (result.success) {
            this._updateUI(result.status);
            showToast(__('config.ilinkbot.stopped'), 'success');
        }
    },

    async logout() {
        const result = await apiCall('imbot.wechat.logout');
        if (result.success) {
            document.getElementById('ilinkbotStartBtn').style.display = 'none';
            document.getElementById('ilinkbotStopBtn').style.display = 'none';
            document.getElementById('ilinkbotLogoutBtn').style.display = 'none';
            document.getElementById('ilinkbotQrArea').style.display = 'none';
            this._updateStatusBadge(false, false);
            showToast(__('config.ilinkbot.loggedOut'), 'success');
        }
    },

    async send() {
        const input = document.getElementById('ilinkbotMsgInput');
        const content = input.value.trim();
        if (!content) return;

        const result = await apiCall('imbot.wechat.send', { content });
        if (result.success) {
            input.value = '';
            this._appendMessage({ direction: 'outgoing', content, msg_type: 'text', timestamp: new Date().toISOString() });
        } else {
            showToast(result.error || __('config.ilinkbot.sendFailed'), 'error');
        }
    },

    async loadMessages() {
        const result = await apiCall('imbot.wechat.messages', { limit: 50 });
        if (result.success && result.messages) {
            const box = document.getElementById('ilinkbotChatBox');
            box.innerHTML = '';
            if (result.messages.length === 0) {
                box.innerHTML = '<p class="text-muted" style="text-align:center;padding:40px 0;">' + __('config.ilinkbot.noMessages') + '</p>';
                return;
            }
            result.messages.reverse().forEach(m => this._appendMessage(m));
        }
    },

    _appendMessage(msg) {
        const box = document.getElementById('ilinkbotChatBox');
        const noMsg = box.querySelector('.text-muted');
        if (noMsg) noMsg.remove();

        const div = document.createElement('div');
        const isOut = msg.direction === 'outgoing';
        div.style.cssText = 'margin-bottom:8px;display:flex;flex-direction:column;' + (isOut ? 'align-items:flex-end;' : 'align-items:flex-start;');

        const sender = msg.sender_name || msg.sender_id || '';
        if (!isOut && sender) {
            const nameEl = document.createElement('small');
            nameEl.className = 'text-muted';
            nameEl.textContent = sender;
            div.appendChild(nameEl);
        }

        const bubble = document.createElement('div');
        bubble.style.cssText = 'padding:6px 12px;border-radius:12px;max-width:70%;word-break:break-word;font-size:13px;' +
            (isOut ? 'background:#07c160;color:white;' : 'background:white;border:1px solid var(--border);');
        bubble.textContent = msg.content || '';
        div.appendChild(bubble);

        if (msg.timestamp) {
            const ts = document.createElement('small');
            ts.className = 'text-muted';
            ts.style.fontSize = '11px';
            ts.textContent = new Date(msg.timestamp).toLocaleTimeString();
            div.appendChild(ts);
        }

        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    },

    _connectSSE() {
        if (this._es) return;
        const es = new EventSource('/api/imbot-stream?channel=wechat');
        this._es = es;
        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'message') this._appendMessage(data);
            } catch (e) { /* ignore parse errors */ }
        };
        es.onerror = () => {
            es.close();
            this._es = null;
            setTimeout(() => this._connectSSE(), 5000);
        };
    },

    _updateUI(status) {
        if (!status) return;
        const running = status.is_running;
        const authed = status.is_authenticated;

        document.getElementById('ilinkbotStartBtn').style.display = (!running && authed) ? '' : 'none';
        document.getElementById('ilinkbotStopBtn').style.display = running ? '' : 'none';
        document.getElementById('ilinkbotLogoutBtn').style.display = authed ? '' : 'none';
        document.getElementById('ilinkbotQrArea').style.display = 'none';

        if (status.poll_timeout) {
            document.getElementById('ilinkbotPollTimeout').value = status.poll_timeout;
        }

        this._updateStatusBadge(running, authed);
        if (running) this.loadMessages();
    },

    _updateStatusBadge(running, authed) {
        const badge = document.getElementById('ilinkbotStatusBadge');
        if (running) {
            badge.textContent = __('config.ilinkbot.connected');
            badge.className = 'badge badge-success';
        } else if (authed) {
            badge.textContent = __('config.ilinkbot.authenticated');
            badge.className = 'badge badge-info';
        } else {
            badge.textContent = __('config.ilinkbot.disconnected');
            badge.className = 'badge badge-secondary';
        }
    }
};
