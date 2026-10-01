/* Helot Farm dashboard (Vue 3 global build, без сборщиков). */
const { createApp } = Vue;

// Держим в синхроне с panel/models.py::TaskType.
const TASK_TYPES = ["REGISTER_ACCOUNT", "SEND_MESSAGE", "JOIN_GROUP", "WARMUP"];

createApp({
  data() {
    return {
      devices: [],
      accounts: [],
      tasks: [],
      taskTypes: TASK_TYPES,
      newPhone: "",
      taskAccountId: "",
      taskType: "REGISTER_ACCOUNT",
      taskPayload: '{"phone_number": ""}',
      error: "",
      timer: null,
      // --- C2-стриминг ---
      managing: null,
      streamWs: null,
      player: null,
      streamDims: null,
      streamStatus: "idle",
      streamError: "",
      helotWsBase: `ws://${location.hostname}:8080`,
      dragStart: null,
    };
  },
  methods: {
    statusClass(s) {
      return {
        ONLINE: "bg-success", ACTIVE: "bg-success", COMPLETED: "bg-success",
        BUSY: "bg-warning", IN_PROGRESS: "bg-warning", PENDING: "bg-secondary",
        OFFLINE: "bg-dark", NEW: "bg-info", REGISTERED: "bg-primary",
        FAILED: "bg-danger", BANNED: "bg-danger",
      }[s] || "bg-secondary";
    },
    async loadAll() {
      try {
        const [d, a, t] = await Promise.all([
          fetch("/devices").then((r) => r.json()),
          fetch("/accounts").then((r) => r.json()),
          fetch("/tasks").then((r) => r.json()),
        ]);
        this.devices = d;
        this.accounts = a;
        this.tasks = t;
        this.error = "";
      } catch (e) {
        this.error = "API недоступен: " + e;
      }
    },
    async addAccount() {
      try {
        const r = await fetch("/accounts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone_number: this.newPhone }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || r.status);
        this.newPhone = "";
        await this.loadAll();
      } catch (e) {
        this.error = "Не удалось добавить аккаунт: " + e;
      }
    },
    async createTask() {
      let payload;
      try {
        payload = this.taskPayload.trim() ? JSON.parse(this.taskPayload) : {};
      } catch (e) {
        this.error = "Payload — невалидный JSON";
        return;
      }
      try {
        const r = await fetch("/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: Number(this.taskAccountId),
            task_type: this.taskType,
            payload,
          }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || r.status);
        await this.loadAll();
      } catch (e) {
        this.error = "Не удалось создать задачу: " + e;
      }
    },
  },
    // --- C2: интерактивный стрим ---
    openManage(d) {
      this.stopStreaming();
      this.managing = d;
      this.streamError = "";
      bootstrap.Modal.getOrCreateInstance(document.getElementById("streamModal")).show();
      this.startStreaming(d.serial);
    },
    closeManage() {
      this.stopStreaming();
      bootstrap.Modal.getInstance(document.getElementById("streamModal")).hide();
    },
    startStreaming(serial) {
      this.streamStatus = "connecting";
      const ws = new WebSocket(`${this.helotWsBase}/stream/${serial}`);
      ws.binaryType = "arraybuffer";
      this.streamWs = ws;
      ws.onopen = () => { this.streamStatus = "waiting video…"; };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          let msg;
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === "hello") {
            this.streamDims = { w: msg.width, h: msg.height };
            this.attachPlayer(msg.width, msg.height);
            this.streamStatus = "live";
          } else if (msg.type === "error") {
            this.streamError = msg.message;
            this.streamStatus = "error";
          }
          return;
        }
        if (!this.player) return; // кадры до hello отбрасываем
        this.player.decode(new Uint8Array(ev.data));
      };
      ws.onclose = () => { if (this.streamStatus !== "error") this.streamStatus = "closed"; };
      ws.onerror = () => { this.streamError = "WebSocket недоступен"; this.streamStatus = "error"; };
    },
    attachPlayer(w, h) {
      this.detachPlayer();
      // Broadway Player (cdnjs): декодирует annex-b H.264 в свой canvas.
      this.player = new Player({ size: { width: w, height: h } });
      const holder = document.getElementById("stream-holder");
      holder.appendChild(this.player.canvas);
      const cv = this.player.canvas;
      cv.addEventListener("mousedown", (e) => { this.dragStart = this.toDevice(e); });
      cv.addEventListener("mouseup", (e) => {
        if (!this.dragStart) return;
        const end = this.toDevice(e);
        const dx = end.x - this.dragStart.x, dy = end.y - this.dragStart.y;
        if (Math.hypot(dx, dy) > 12) {
          this.sendInput({ type: "swipe", x1: this.dragStart.x, y1: this.dragStart.y,
                           x2: end.x, y2: end.y, duration: 0.4 });
        } else {
          this.sendInput({ type: "click", x: end.x, y: end.y });
        }
        this.dragStart = null;
      });
    },
    detachPlayer() {
      const holder = document.getElementById("stream-holder");
      if (holder) holder.innerHTML = "";
      this.player = null;
      this.streamDims = null;
    },
    toDevice(e) {
      // canvas-пиксели -> пиксели устройства (масштаб по hello-кадру).
      const r = e.target.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
      return {
        x: Math.round(fx * (this.streamDims ? this.streamDims.w : r.width)),
        y: Math.round(fy * (this.streamDims ? this.streamDims.h : r.height)),
      };
    },
    sendInput(msg) {
      if (this.streamWs && this.streamWs.readyState === WebSocket.OPEN) {
        this.streamWs.send(JSON.stringify(msg));
      }
    },
    stopStreaming() {
      if (this.streamWs) { try { this.streamWs.close(); } catch {} this.streamWs = null; }
      this.detachPlayer();
      this.managing = null;
      this.streamStatus = "idle";
      this.dragStart = null;
    },
  },
  mounted() {
    this.loadAll();
    this.timer = setInterval(this.loadAll, 5000);
    document.getElementById("streamModal").addEventListener("hidden.bs.modal", () => this.stopStreaming());
  },
  unmounted() {
    clearInterval(this.timer);
  },
}).mount("#app");
