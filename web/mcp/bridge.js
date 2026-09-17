import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const PREFIX = "/arkennemasis/mcp/browser";
let enabled = false;
let session = null;
let connecting = false;
let maintaining = false;
let consentVersion = 0;
let chain = Promise.resolve();
let badge = null;
let label = null;
let toggleButton = null;
let connectionLabel = null;
let checkingConnection = false;
const undo = [];
const workflowIdentities = new WeakMap();
const operations = new Map();

function status(text) {
  if (!badge) return;
  label.textContent = `Arkennemasis MCP: ${text}`;
}

async function post(path, body, keepalive = false) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await api.fetchApi(`${PREFIX}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ark-Canvas": "1" },
    body: JSON.stringify(body),
    keepalive,
    signal: controller.signal,
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error?.message || `Canvas connection failed (${response.status}).`),
    { code: result.error?.code, status: response.status });
  return result;
  } finally {
    clearTimeout(timer);
  }
}

async function refreshConnectionStatus() {
  if (checkingConnection || !connectionLabel) return;
  checkingConnection = true;
  try {
    const result = await post("connection-status", {});
    const labels = { waiting: "starting", starting: "starting", online: "online", reconnecting: "reconnecting",
      restarting: "ComfyUI restarting", degraded: "needs attention", stopped: "offline", error: "error" };
    const state = labels[result.state] ?? "offline";
    connectionLabel.textContent = `Connection: ${state}`;
    connectionLabel.title = result.message || "Start ComfyUI with its BAT file to enable the connection.";
    connectionLabel.style.color = state === "online" ? "#91e6b0" : state === "error" ? "#ffb0a9" : "#b8c8dd";
  } catch {
    connectionLabel.textContent = "Connection: unavailable";
    connectionLabel.title = "Connection status is unavailable. Relaunch ComfyUI with its BAT file.";
    connectionLabel.style.color = "#b8c8dd";
  } finally {
    checkingConnection = false;
  }
}

function sorted(value) {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sorted(value[key])]));
  }
  return value;
}

function serialized(graph) {
  return JSON.stringify(sorted(graph.serialize()));
}

function activeWorkflow() {
  return app.extensionManager?.workflow?.activeWorkflow ?? null;
}

async function snapshot() {
  const graph = app.rootGraph ?? app.graph;
  if (!graph) throw new Error("The ComfyUI workflow is not loaded yet.");
  const active = activeWorkflow();
  const identityOwner = active ?? graph;
  if (!workflowIdentities.has(identityOwner)) workflowIdentities.set(identityOwner, crypto.randomUUID());
  const before = serialized(graph);
  const workflow = JSON.parse(before);
  let prompt = null;
  let promptError = null;
  try {
    prompt = (await app.graphToPrompt(graph)).output;
  } catch (error) {
    promptError = error.message;
  }
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
    workflowIdentities.get(identityOwner) + ":" + before));
  if (graph !== (app.rootGraph ?? app.graph) || active !== activeWorkflow() || before !== serialized(graph)) {
    throw new Error("The canvas changed while it was being read. Read it again before editing.");
  }
  return {
    workflow,
    prompt,
    prompt_error: promptError,
    revision: Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, "0")).join(""),
    nodes: (graph._nodes ?? []).map((node) => ({
      id: node.id,
      type: node.comfyClass ?? node.type,
      title: node.title,
      widgets: (node.widgets ?? []).map((widget, index) => ({
        name: widget.name,
        value: widget.value ?? null,
        index,
        serialized: widget.options?.serialize !== false,
      })),
    })),
    info: { title: document.title, workflow_id: workflowIdentities.get(identityOwner), workflow_name: active?.path ?? active?.filename ?? null,
      undo_available: undo.some((previous) => previous.active === active) },
  };
}

function assertCurrent(request, owner) {
  if (!enabled || session !== owner) throw new Error("Canvas sharing has been disabled or reconnected.");
  if (Date.now() / 1000 >= request.expires_at) throw new Error("The canvas request expired. Read the canvas before retrying.");
}

function blockInput() {
  const overlay = document.createElement("div");
  overlay.setAttribute("role", "status");
  overlay.textContent = "Arkennemasis MCP is applying a workflow change…";
  Object.assign(overlay.style, {
    position: "fixed", inset: "0", zIndex: "2147483646", background: "rgba(0,0,0,.35)",
    color: "white", display: "grid", placeItems: "center", font: "16px sans-serif", cursor: "wait",
  });
  const stopKey = (event) => { event.preventDefault(); event.stopImmediatePropagation(); };
  window.addEventListener("keydown", stopKey, true);
  document.body.append(overlay);
  return () => { overlay.remove(); window.removeEventListener("keydown", stopKey, true); };
}

function verifyLoaded(target, actual) {
  const actualNodes = new Map(actual.nodes.map((node) => [String(node.id), node]));
  if (target.nodes.length !== actualNodes.size) throw new Error("ComfyUI did not load the requested node set.");
  for (const requested of target.nodes) {
    const loaded = actualNodes.get(String(requested.id));
    if (!loaded || loaded.type !== requested.type) throw new Error("ComfyUI did not load the requested node type.");
    for (const field of ["widgets_values", "title", "mode"]) {
      if (field in requested && JSON.stringify(sorted(loaded[field])) !== JSON.stringify(sorted(requested[field]))) {
        throw new Error(`ComfyUI changed ${field} on node ${requested.id} while loading. Read the canvas to inspect it; undo is available.`);
      }
    }
  }
  const links = (workflow) => (workflow.links ?? []).map((link) => Array.isArray(link) ? link :
    [link.id, link.origin_id, link.origin_slot, link.target_id, link.target_slot, link.type]).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
  if (JSON.stringify(links(target)) !== JSON.stringify(links(actual))) {
    throw new Error("ComfyUI did not preserve the requested connections. Read the canvas to inspect it; undo is available.");
  }
}

async function execute(request, owner) {
  assertCurrent(request, owner);
  const before = await snapshot();
  assertCurrent(request, owner);
  if (request.command === "read") return before;
  if (request.expected_revision !== before.revision) {
    return { error: { code: "revision_conflict", message: "The canvas changed since the last read. Read it again before editing.", current_revision: before.revision } };
  }
  const active = activeWorkflow();
  if (!active) throw new Error("The current workflow tab could not be identified. Open a workflow before editing.");
  const tracker = active.changeTracker;
  if (!tracker || typeof tracker.updateModified !== "function") {
    throw new Error("This ComfyUI frontend cannot preserve the workflow's unsaved state. Update the frontend before editing.");
  }
  const savedBaseline = tracker.initialState;
  let target = request.workflow;
  const undoIndex = undo.findLastIndex((previous) => previous.active === active);
  if (request.command === "undo") {
    const previous = undo[undoIndex];
    if (!previous) throw new Error("No MCP undo snapshot exists for this workflow tab.");
    target = previous.workflow;
  }
  if (!target || !Array.isArray(target.nodes)) throw new Error("An edit requires a ComfyUI workflow with a nodes list.");
  const release = blockInput();
  // Keep a snapshot even when loading or acknowledgment fails: the edit may be partial.
  if (request.command === "apply") {
    undo.push({ active, workflow: before.workflow });
    if (undo.length > 10) undo.shift();
  }
  try {
    status(request.command === "undo" ? "restoring workflow" : "applying change");
    await app.loadGraphData(structuredClone(target), false, false, active, { skipAssetScans: true });
    if (active !== activeWorkflow()) throw new Error("The selected workflow tab changed during the edit. Inspect the original tab before retrying.");
    const after = await snapshot();
    verifyLoaded(target, after.workflow);
    if (request.command === "undo") undo.splice(undoIndex, 1);
    after.info.undo_available = undo.some((previous) => previous.active === active);
    after.edit = { verified: true, command: request.command, previous_revision: before.revision,
      revision: after.revision, workflow_id: after.info.workflow_id };
    status("canvas shared · change applied");
    return after;
  } finally {
    try {
      if (active === activeWorkflow()) {
        tracker.initialState = savedBaseline;
        tracker.activeState = JSON.parse(serialized(app.rootGraph ?? app.graph));
        // No previous-state argument: notify the draft without triggering auto-queue.
        tracker.updateModified();
      }
    } finally {
      release();
    }
  }
}

async function handle(request) {
  const owner = session;
  if (!enabled || !owner || request.session_id !== owner.session_id) return;
  const key = `${owner.session_id}:${request.request_id}`;
  const fingerprint = JSON.stringify(sorted({ command: request.command, expected_revision: request.expected_revision, workflow: request.workflow }));
  for (const [id, previous] of operations) if (previous.expires < Date.now()) operations.delete(id);
  let operation = operations.get(key);
  if (operation && operation.fingerprint !== fingerprint) {
    status("A repeated request changed its contents; no additional edit was applied.");
    return;
  }
  if (!operation) {
    if (operations.size >= 64) {
      status("Too many recent canvas requests. Wait before trying again.");
      return;
    }
    operation = { owner, request_id: request.request_id, fingerprint, expires: Date.now() + 300000, acknowledged: false };
    operation.result = execute(request, owner).catch((error) => {
      status(error.message);
      return { error: { code: "canvas_error", message: error.message } };
    });
    operations.set(key, operation);
  }
  await acknowledge(operation);
}

async function acknowledge(operation) {
  const result = await operation.result;
  if (!enabled || session !== operation.owner || operation.acknowledged) return;
  try {
    await post("ack", { ...operation.owner, request_id: operation.request_id, result });
    operation.acknowledged = true;
  } catch (error) {
    if (error.code === "request_expired" || error.code === "acknowledgment_conflict") operation.acknowledged = true;
    status(`acknowledgment pending · ${error.message}`);
  }
}

function enqueue(request) {
  chain = chain.then(() => handle(request)).catch((error) => status(error.message));
}

async function connect() {
  if (!enabled || connecting || session || !api.clientId) return;
  connecting = true;
  const version = consentVersion;
  try {
    const registered = await post("register", { client_id: api.clientId, title: document.title });
    if (!enabled || version !== consentVersion) {
      await post("disconnect", registered);
      return;
    }
    session = { session_id: registered.session_id, session_secret: registered.session_secret };
    status("canvas shared");
  } catch (error) {
    status(error.message);
  } finally {
    connecting = false;
    if (enabled && version !== consentVersion) void connect();
  }
}

async function maintainSession() {
  if (!enabled || maintaining) return;
  if (!session) { await connect(); return; }
  maintaining = true;
  const owner = session;
  try {
    const result = await post("heartbeat", { ...owner, client_id: api.clientId, title: document.title });
    if (!enabled || owner !== session) return;
    status("canvas shared");
    for (const request of result.commands ?? []) enqueue(request);
    for (const operation of operations.values()) {
      if (operation.expires < Date.now()) continue;
      if (!operation.acknowledged && operation.owner === owner) await acknowledge(operation);
    }
  } catch (error) {
    if (!enabled || owner !== session) return;
    status(`canvas reconnecting · ${error.message}`);
    // A network failure does not revoke consent or replace the selected session.
    if (error.code === "unknown_session") {
      session = null;
      operations.clear();
      await connect();
    }
  } finally {
    maintaining = false;
  }
}

async function setEnabled(value) {
  if (enabled !== Boolean(value)) consentVersion++;
  enabled = Boolean(value);
  toggleButton.textContent = enabled ? "Stop sharing" : "Share canvas";
  if (!enabled) {
    const previous = session;
    session = null;
    undo.length = 0;
    operations.clear();
    status("not shared");
    if (previous) await post("disconnect", previous).catch(() => {});
    return;
  }
  status("connecting");
  await connect();
}

app.registerExtension({
  name: "arkennemasis.mcp.canvas",
  async setup() {
    badge = document.createElement("div");
    badge.setAttribute("role", "status");
    Object.assign(badge.style, {
      position: "fixed", bottom: "12px", left: "72px", zIndex: "1000", padding: "8px 10px",
      borderRadius: "8px", background: "#17202b", color: "#e7effb", border: "1px solid #425773",
      maxWidth: "min(620px, 80vw)", font: "12px sans-serif",
    });
    label = document.createElement("span");
    toggleButton = document.createElement("button");
    toggleButton.style.marginLeft = "12px";
    toggleButton.title = "Share this tab with authenticated Arkennemasis MCP clients until stopped or refreshed.";
    toggleButton.onclick = () => { void setEnabled(!enabled); };
    badge.append(label, toggleButton);
    connectionLabel = document.createElement("span");
    connectionLabel.textContent = "Connection: checking…";
    Object.assign(connectionLabel.style, { display: "block", marginTop: "5px" });
    badge.append(connectionLabel);
    document.body.append(badge);
    api.addEventListener("arkennemasis.mcp.canvas", (event) => enqueue(event.detail));
    api.addEventListener("reconnected", () => { void maintainSession(); });
    api.addEventListener("status", () => { if (!session) void connect(); });
    setInterval(maintainSession, 15000);
    setInterval(refreshConnectionStatus, 5000);
    window.addEventListener("online", () => { void maintainSession(); void refreshConnectionStatus(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") { void maintainSession(); void refreshConnectionStatus(); }
    });
    window.addEventListener("pagehide", () => {
      if (session) void post("disconnect", session, true).catch(() => {});
      session = null;
      void setEnabled(false);
    });
    // Opt-in is per page, never inherited from another tab's persisted settings.
    await setEnabled(false);
    await refreshConnectionStatus();
  },
});
