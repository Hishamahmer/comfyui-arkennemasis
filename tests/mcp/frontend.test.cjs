const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { isDeepStrictEqual } = require("node:util");

const sourcePath = path.resolve(__dirname, "../../web/mcp/bridge.js");
const source = fs.readFileSync(sourcePath, "utf8")
  .replace(/^import \{ (?:app|api) \} from "\.\.\/\.\.\/\.\.\/scripts\/(?:app|api)\.js";\r?\n/gm, "");

const plain = (value) => JSON.parse(JSON.stringify(value));

function workflow(value = "original") {
  return {
    nodes: [{ id: 1, type: "CLIPTextEncode", title: "Prompt", widgets_values: [value], mode: 0 }],
    links: [],
    version: 0.4,
  };
}

function element(tag) {
  return {
    tag, style: {}, children: [], attributes: {}, textContent: "", parent: null,
    setAttribute(name, value) { this.attributes[name] = value; },
    append(...children) {
      for (const child of children) { child.parent = this; this.children.push(child); }
    },
    remove() {
      if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
      this.parent = null;
    },
  };
}

async function harness(initial = workflow()) {
  const tab = { path: "workflows/first.json" };
  const graphs = new Map([[tab, structuredClone(initial)]]);
  const calls = [];
  const listeners = new Map();
  const windowListeners = new Map();
  const documentListeners = new Map();
  const intervals = [];
  const body = element("body");
  function attachTracker(selected, graph) {
    selected.isModified = false;
    selected.changeTracker = {
      initialState: structuredClone(graph),
      activeState: structuredClone(graph),
      reset(data) {
        this.activeState = structuredClone(data);
        this.initialState = structuredClone(data);
        calls.push({ operation: "resetBaseline", tab: selected, baseline: structuredClone(data) });
      },
      updateModified(...args) {
        calls.push({ operation: "updateModified", tab: selected, args });
        selected.isModified = !isDeepStrictEqual(plain(this.initialState), plain(this.activeState));
      },
    };
  }
  attachTracker(tab, initial);
  let extension;
  let promptFailure = null;
  let duringConversion = null;
  let loadBehavior = null;
  let registrations = 0;
  let connectionStatus = { state: "stopped", message: "Start ComfyUI with its BAT file.", updated_at: null };
  let connectionFailure = false;
  let fetchBehavior = null;
  const app = {
    extensionManager: { workflow: { activeWorkflow: tab } },
    registerExtension(value) { extension = value; },
    async graphToPrompt() {
      if (duringConversion) await duringConversion();
      if (promptFailure) throw new Error(promptFailure);
      return { workflow: app.rootGraph.serialize(), output: { "1": { class_type: "CLIPTextEncode", inputs: { text: app.rootGraph.serialize().nodes[0]?.widgets_values[0] } } } };
    },
    async loadGraphData(data, ...options) {
      calls.push({ operation: "load", data: structuredClone(data), options });
      if (loadBehavior) await loadBehavior(data);
      else graphs.set(app.extensionManager.workflow.activeWorkflow, structuredClone(data));
      // Installed ComfyUI calls reset on the current tracker after loading a graph.
      app.extensionManager.workflow.activeWorkflow.changeTracker.reset(app.rootGraph.serialize());
    },
  };
  app.rootGraph = {
    serialize() { return structuredClone(graphs.get(app.extensionManager.workflow.activeWorkflow)); },
    get _nodes() {
      return this.serialize().nodes.map((node) => ({
        id: node.id, type: node.type, title: node.title,
        widgets: [{ name: "text", value: node.widgets_values[0], options: {} }],
      }));
    },
  };
  const api = {
    clientId: "browser-test-id",
    addEventListener(name, callback) { listeners.set(name, callback); },
    async fetchApi(url, options) {
      const payload = JSON.parse(options.body);
      calls.push({ operation: "fetch", url, options, payload });
      if (fetchBehavior) {
        const custom = await fetchBehavior(url, payload);
        if (custom) return custom;
      }
      if (url.endsWith("/connection-status")) {
        if (connectionFailure) throw new Error("Connection unavailable");
        return { ok: true, status: 200, async json() { return connectionStatus; } };
      }
      const result = url.endsWith("/register")
        ? { session_id: `session-${++registrations}`, session_secret: `private-${registrations}` }
        : { acknowledged: true };
      return { ok: true, status: 200, async json() { return result; } };
    },
  };
  const context = vm.createContext({
    app, api, crypto: webcrypto, TextEncoder, structuredClone, AbortController, setTimeout, clearTimeout,
    document: { title: "ComfyUI test", body, createElement: element, visibilityState: "visible",
      addEventListener(name, callback) { documentListeners.set(name, callback); } },
    window: {
      addEventListener(name, callback) { windowListeners.set(name, callback); },
      removeEventListener(name, callback) { if (windowListeners.get(name) === callback) windowListeners.delete(name); },
    },
    setInterval(callback) { intervals.push(callback); },
  });
  vm.runInContext(source + "\n globalThis.testBridge = { snapshot, execute, setEnabled, handle, maintainSession, refreshConnectionStatus, drained: () => chain, owner: () => session };", context, { filename: sourcePath });
  await extension.setup();
  const bridge = context.testBridge;
  return {
    app, api, bridge, tab, graphs, calls, body, listeners, windowListeners, documentListeners, intervals,
    async share() { await bridge.setEnabled(true); },
    select(selected) { app.extensionManager.workflow.activeWorkflow = selected; },
    addTab(name, graph = initial) {
      const selected = { path: `workflows/${name}.json` };
      graphs.set(selected, structuredClone(graph));
      attachTracker(selected, graph);
      return selected;
    },
    failPrompt(message) { promptFailure = message; },
    duringPrompt(callback) { duringConversion = callback; },
    onLoad(callback) { loadBehavior = callback; },
    connectionStatus(value) { connectionStatus = value; connectionFailure = false; },
    failConnection() { connectionFailure = true; },
    onFetch(callback) { fetchBehavior = callback; },
    async run(command, values = {}) {
      return bridge.execute({ command, expires_at: Date.now() / 1000 + 30, ...values }, bridge.owner());
    },
  };
}

test("each new browser page starts unshared and does not register on status or timer events", async () => {
  const first = await harness();
  assert.equal(first.bridge.owner(), null);
  assert.equal(first.body.children[0].children[1].textContent, "Share canvas");
  first.listeners.get("status")({ detail: {} });
  await first.intervals[0]();
  assert.equal(first.calls.filter((call) => !call.url?.endsWith("/connection-status")).length, 0);
  await first.share();
  assert.equal(first.bridge.owner().session_id, "session-1");
  const second = await harness();
  assert.equal(second.bridge.owner(), null);
  assert.equal(second.calls.filter((call) => !call.url?.endsWith("/connection-status")).length, 0);
});

test("connection readiness is shown and refreshed independently of canvas sharing", async () => {
  const h = await harness();
  const badge = h.body.children[0];
  const connection = badge.children[2];
  assert.equal(connection.textContent, "Connection: offline");
  assert.equal(h.calls.filter((call) => call.url?.endsWith("/connection-status")).length, 1);
  for (const [state, label] of [["waiting", "starting"], ["starting", "starting"], ["online", "online"], ["error", "error"], ["stopped", "offline"]]) {
    h.connectionStatus({ state, message: `Current state: ${state}` });
    await h.intervals[1]();
    assert.equal(connection.textContent, `Connection: ${label}`);
    assert.equal(connection.title, `Current state: ${state}`);
    assert.equal(h.bridge.owner(), null);
    assert.equal(badge.children[1].textContent, "Share canvas");
  }
  await h.share();
  const owner = h.bridge.owner();
  h.failConnection();
  await h.bridge.refreshConnectionStatus();
  assert.equal(connection.textContent, "Connection: unavailable");
  assert.equal(h.bridge.owner(), owner, "Status failure must not revoke canvas sharing");
  assert.equal(badge.children[1].textContent, "Stop sharing");
});

test("identical workflow content in a different tab invalidates a previous edit revision", async () => {
  const h = await harness();
  await h.share();
  const first = await h.run("read");
  const secondTab = h.addTab("clone");
  h.select(secondTab);
  const second = await h.run("read");
  assert.deepEqual(plain(first.workflow), plain(second.workflow));
  assert.notEqual(first.revision, second.revision);
  const response = await h.run("apply", { expected_revision: first.revision, workflow: workflow("new") });
  assert.equal(response.error.code, "revision_conflict");
  assert.equal(h.calls.filter((call) => call.operation === "load").length, 0);
  h.select(h.tab);
  assert.equal((await h.run("read")).revision, first.revision);
});

test("broken API prompt conversion preserves readable UI workflow, revision and widget names", async () => {
  const h = await harness();
  await h.share();
  const before = await h.run("read");
  h.failPrompt("Custom node is missing");
  const result = await h.run("read");
  assert.deepEqual(plain(result.workflow), workflow());
  assert.equal(result.prompt, null);
  assert.equal(result.prompt_error, "Custom node is missing");
  assert.equal(result.revision, before.revision);
  assert.equal(result.nodes[0].widgets[0].name, "text");
  assert.equal(result.nodes[0].widgets[0].value, "original");
});

test("a swallowed load failure cannot report a widget edit as applied and retains undo", async () => {
  const h = await harness();
  await h.share();
  h.onLoad(async () => {});
  const before = await h.run("read");
  await assert.rejects(h.run("apply", { expected_revision: before.revision, workflow: workflow("new") }), /widgets_values on node 1/);
  const after = await h.run("read");
  assert.equal(after.workflow.nodes[0].widgets_values[0], "original");
  assert.equal(after.info.undo_available, true);
  assert.equal(h.body.children.length, 1, "The temporary input blocker must be removed after failure");
  assert.equal(h.windowListeners.has("keydown"), false);
});

test("undo selects the current tab's snapshot when edits from other tabs are interleaved", async () => {
  const h = await harness(workflow("A original"));
  await h.share();
  await h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: workflow("A edited") });
  const tabB = h.addTab("second", workflow("B original"));
  h.select(tabB);
  assert.equal((await h.run("read")).info.undo_available, false);
  await h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: workflow("B edited") });
  h.select(h.tab);
  const restoredA = await h.run("undo", { expected_revision: (await h.run("read")).revision });
  assert.equal(restoredA.workflow.nodes[0].widgets_values[0], "A original");
  assert.equal(restoredA.info.undo_available, false);
  assert.equal(h.graphs.get(tabB).nodes[0].widgets_values[0], "B edited");
  h.select(tabB);
  assert.equal((await h.run("read")).info.undo_available, true);
  const restoredB = await h.run("undo", { expected_revision: (await h.run("read")).revision });
  assert.equal(restoredB.workflow.nodes[0].widgets_values[0], "B original");
});

test("undo refuses a different tab without modifying it", async () => {
  const h = await harness();
  await h.share();
  await h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: workflow("edited") });
  const secondTab = h.addTab("unrelated", workflow("unrelated"));
  h.select(secondTab);
  const reads = await h.run("read");
  assert.equal(reads.info.undo_available, false);
  const loadCount = h.calls.filter((call) => call.operation === "load").length;
  await assert.rejects(h.run("undo", { expected_revision: reads.revision }), /No MCP undo snapshot exists for this workflow tab/);
  assert.equal(h.calls.filter((call) => call.operation === "load").length, loadCount);
  assert.equal(h.graphs.get(secondTab).nodes[0].widgets_values[0], "unrelated");
});

test("load verification detects changed node types and missing connections", async () => {
  for (const change of ["type", "links"]) {
    const h = await harness();
    await h.share();
    const target = workflow();
    if (change === "type") target.nodes[0].type = "DifferentNode";
    else target.links = [[1, 1, 0, 1, 0, "CLIP"]];
    h.onLoad(async () => {});
    await assert.rejects(h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: target }), change === "type" ? /node type/ : /connections/);
  }
});

test("a user edit during prompt conversion invalidates the snapshot", async () => {
  const h = await harness();
  await h.share();
  h.duringPrompt(async () => h.graphs.set(h.tab, workflow("changed during read")));
  await assert.rejects(h.run("read"), /canvas changed while it was being read/);
});

test("stopping sharing revokes a previously queued edit and clears its undo snapshots", async () => {
  const h = await harness();
  await h.share();
  const owner = h.bridge.owner();
  const before = await h.run("read");
  await h.bridge.setEnabled(false);
  await assert.rejects(h.bridge.execute({ command: "apply", workflow: workflow("new"), expected_revision: before.revision, expires_at: Date.now() / 1000 + 30 }, owner), /disabled or reconnected/);
  assert.equal(h.calls.filter((call) => call.operation === "load").length, 0);
  assert.equal(h.calls.at(-1).url.endsWith("/disconnect"), true);
});

test("an MCP edit preserves the saved baseline, marks the tab dirty, and undo returns it to clean without auto-queue notification", async () => {
  const h = await harness(workflow("saved"));
  await h.share();
  const baseline = h.tab.changeTracker.initialState;
  const applied = await h.run("apply", {
    expected_revision: (await h.run("read")).revision,
    workflow: workflow("MCP edit"),
  });
  const reset = h.calls.find((call) => call.operation === "resetBaseline");
  assert.equal(reset.baseline.nodes[0].widgets_values[0], "MCP edit", "The fixture must reproduce ComfyUI replacing the baseline during load");
  assert.equal(h.tab.changeTracker.initialState, baseline, "The original saved baseline object must survive the load");
  assert.equal(h.tab.changeTracker.initialState.nodes[0].widgets_values[0], "saved");
  assert.equal(h.tab.changeTracker.activeState.nodes[0].widgets_values[0], "MCP edit");
  assert.equal(h.tab.isModified, true);
  const restored = await h.run("undo", { expected_revision: applied.revision });
  assert.equal(restored.workflow.nodes[0].widgets_values[0], "saved");
  assert.equal(h.tab.changeTracker.initialState, baseline);
  assert.equal(h.tab.isModified, false);
  const updates = h.calls.filter((call) => call.operation === "updateModified");
  assert.equal(updates.length, 2);
  for (const call of updates) assert.equal(call.args.length, 0, "Passing previous state can trigger ComfyUI auto-queue");
});

test("undo preserves a preexisting unsaved user draft and its original saved baseline", async () => {
  const h = await harness(workflow("saved"));
  await h.share();
  h.graphs.set(h.tab, workflow("user draft"));
  h.tab.changeTracker.activeState = workflow("user draft");
  h.tab.isModified = true;
  await h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: workflow("MCP edit") });
  await h.run("undo", { expected_revision: (await h.run("read")).revision });
  assert.equal(h.tab.changeTracker.initialState.nodes[0].widgets_values[0], "saved");
  assert.equal(h.tab.changeTracker.activeState.nodes[0].widgets_values[0], "user draft");
  assert.equal(h.tab.isModified, true);
});

test("socket reconnection and failed heartbeat retain the selected session", async () => {
  const h = await harness();
  await h.share();
  const owner = h.bridge.owner();
  h.onFetch(async (url) => { if (url.endsWith("/heartbeat")) throw new Error("network interrupted"); });
  await h.bridge.maintainSession();
  assert.equal(h.bridge.owner(), owner);
  h.onFetch(null);
  h.api.clientId = "resumed-client";
  h.listeners.get("reconnected")();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.bridge.owner(), owner);
  const heartbeats = h.calls.filter((call) => call.url?.endsWith("/heartbeat"));
  assert.equal(heartbeats.at(-1).payload.client_id, "resumed-client");
  assert.equal(heartbeats.at(-1).payload.session_secret, owner.session_secret);
  assert.equal(h.calls.filter((call) => call.url?.endsWith("/register")).length, 1);
});

test("unknown session after backend restart re-registers only while still opted in", async () => {
  const h = await harness();
  await h.share();
  h.onFetch(async (url) => url.endsWith("/heartbeat") ?
    { ok: false, status: 403, async json() { return { error: { code: "unknown_session", message: "Reconnect" } }; } } : null);
  await h.bridge.maintainSession();
  assert.equal(h.bridge.owner().session_id, "session-2");
  await h.bridge.setEnabled(false);
  h.listeners.get("reconnected")();
  h.windowListeners.get("online")();
  h.documentListeners.get("visibilitychange")();
  await h.intervals[0]();
  assert.equal(h.bridge.owner(), null);
  assert.equal(h.calls.filter((call) => call.url?.endsWith("/register")).length, 2);
});

test("a heartbeat completing after Stop sharing cannot reconnect or deliver pending edits", async () => {
  const h = await harness();
  await h.share();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  h.onFetch(async (url) => { if (url.endsWith("/heartbeat")) { await pending; throw Object.assign(new Error("old session"), { code: "unknown_session" }); } });
  const heartbeat = h.bridge.maintainSession();
  await h.bridge.setEnabled(false);
  release();
  await heartbeat;
  assert.equal(h.bridge.owner(), null);
  assert.equal(h.calls.filter((call) => call.url?.endsWith("/register")).length, 1);
});

test("duplicate delivery and a lost acknowledgment never apply the same request twice", async () => {
  const h = await harness();
  await h.share();
  const owner = h.bridge.owner();
  const before = await h.run("read");
  const request = { command: "apply", session_id: owner.session_id, request_id: "edit-once", expected_revision: before.revision,
    workflow: workflow("edited once"), expires_at: Date.now() / 1000 + 30 };
  let attempts = 0;
  h.onFetch(async (url) => { if (url.endsWith("/ack") && ++attempts === 1) throw new Error("response lost"); });
  await h.bridge.handle(request);
  await h.bridge.maintainSession();
  await h.bridge.handle(request);
  assert.equal(h.calls.filter((call) => call.operation === "load").length, 1);
  const acknowledgments = h.calls.filter((call) => call.url?.endsWith("/ack"));
  assert.equal(acknowledgments.length, 2);
  assert.deepEqual(acknowledgments[0].payload.result, acknowledgments[1].payload.result);
  const edit = acknowledgments[1].payload.result.edit;
  assert.equal(edit.verified, true);
  assert.equal(edit.previous_revision, before.revision);
  assert.equal(edit.workflow_id, before.info.workflow_id);
  assert.notEqual(edit.revision, before.revision);
});

test("heartbeat delivers a command missed during websocket reconnect with matching session only", async () => {
  const h = await harness();
  await h.share();
  const owner = h.bridge.owner();
  const request = { command: "apply", session_id: owner.session_id, request_id: "missed-event",
    expected_revision: (await h.run("read")).revision, workflow: workflow("recovered"), expires_at: Date.now() / 1000 + 30 };
  h.onFetch(async (url) => url.endsWith("/heartbeat") ? { ok: true, async json() { return { commands: [request, { ...request, session_id: "other-tab" }] }; } } : null);
  await h.bridge.maintainSession();
  await h.bridge.drained();
  await h.bridge.maintainSession();
  await h.bridge.drained();
  assert.equal(h.calls.filter((call) => call.operation === "load").length, 1);
  assert.equal(h.graphs.get(h.tab).nodes[0].widgets_values[0], "recovered");
});

test("new consent cannot adopt an earlier in-flight registration", async () => {
  const h = await harness();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let first = true;
  h.onFetch(async (url) => { if (url.endsWith("/register") && first) { first = false; await pending; } });
  const sharing = h.share();
  await h.bridge.setEnabled(false);
  await h.share();
  release();
  await sharing;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.bridge.owner().session_id, "session-2");
  assert.equal(h.calls.find((call) => call.url?.endsWith("/disconnect")).payload.session_id, "session-1");
});

test("a tab change during loading cannot produce a verified edit receipt for a different workflow", async () => {
  const h = await harness();
  await h.share();
  const other = h.addTab("other");
  h.onLoad(async () => h.select(other));
  await assert.rejects(h.run("apply", { expected_revision: (await h.run("read")).revision, workflow: workflow("new") }), /selected workflow tab changed/);
});
