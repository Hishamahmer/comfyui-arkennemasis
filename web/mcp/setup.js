import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const PREFIX = "/arkennemasis/mcp/setup";
let dialog = null;
let installing = false;

function element(tag, text, parent) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  parent?.append(node);
  return node;
}

async function post(action, body = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await api.fetchApi(`${PREFIX}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Ark-Canvas": "1" },
      body: JSON.stringify(body), signal: controller.signal,
    });
    const text = await response.text();
    let value;
    try { value = JSON.parse(text); } catch { throw new Error(response.ok ? "Setup returned an unexpected response." : text.slice(0, 240)); }
    if (!response.ok) throw new Error(value.error?.message || "Setup could not complete.");
    return value;
  } finally {
    clearTimeout(timeout);
  }
}

async function openSetup() {
  if (dialog) { dialog.showModal(); await dialog.refreshSetup(); return; }
  dialog = element("dialog", undefined, document.body);
  Object.assign(dialog.style, {
    width: "min(580px, 90vw)", maxHeight: "85vh", overflowY: "auto", padding: "24px",
    background: "var(--comfy-menu-bg, #222)", color: "var(--input-text, #eee)",
    border: "1px solid var(--border-color, #555)", borderRadius: "12px", font: "14px system-ui, sans-serif",
  });
  const heading = element("h2", "Arkennemasis AI connection", dialog);
  heading.id = "ark-mcp-setup-heading";
  heading.style.marginTop = "0";
  dialog.setAttribute("aria-labelledby", heading.id);
  element("p", "Set up the optional AI connection. Your other Arkennemasis nodes work independently.", dialog);
  const detection = element("p", "Checking this installation…", dialog);
  Object.assign(detection.style, { whiteSpace: "pre-wrap", overflowWrap: "anywhere", color: "var(--descrip-text, #bbb)" });
  const form = element("form", undefined, dialog);
  form.onsubmit = (event) => event.preventDefault();
  const field = (title, multiline = false) => {
    const label = element("label", title, form);
    Object.assign(label.style, { display: "block", marginTop: "14px" });
    const input = element(multiline ? "textarea" : "input", undefined, label);
    if (!multiline) input.type = "text";
    else input.rows = 3;
    Object.assign(input.style, { display: "block", width: "100%", boxSizing: "border-box", marginTop: "5px", padding: "8px" });
    input.spellcheck = false;
    return input;
  };
  const python = field("Python executable for the optional MCP gateway");
  const publicUrl = field("Fixed public HTTPS address");
  publicUrl.placeholder = "https://your-computer.your-tailnet.ts.net";
  const tunnel = element("p", "", form);
  tunnel.style.fontSize = "12px";
  const permissions = element("fieldset", undefined, form);
  permissions.style.marginTop = "18px";
  element("legend", "Allow this connection to", permissions);
  const scopeInputs = new Map();
  for (const [scope, title] of [
    ["comfy:read", "Read workflows and status"], ["comfy:write", "Edit workflows and shared canvases"],
    ["comfy:run", "Run and control workflows"], ["comfy:media", "Access models and media"],
    ["comfy:develop", "Create and edit selected custom-node code"],
    ["comfy:maintain", "Install packages and perform maintenance"],
  ]) {
    const label = element("label", undefined, permissions);
    Object.assign(label.style, { display: "block", padding: "5px 0" });
    const input = element("input", undefined, label);
    input.type = "checkbox";
    label.append(document.createTextNode(` ${title}`));
    scopeInputs.set(scope, input);
  }
  const packs = field("Allowed custom-node folders (one name per line)", true);
  packs.placeholder = "comfyui-arkennemasis";
  const available = element("p", "", form);
  Object.assign(available.style, { fontSize: "12px", overflowWrap: "anywhere", maxHeight: "60px", overflowY: "auto" });
  element("p", "Development code runs with ComfyUI's computer permissions. The private connection URL grants the permissions selected here; keep it private.", form).style.fontSize = "12px";
  const message = element("p", "", dialog);
  message.setAttribute("role", "status");
  message.style.whiteSpace = "pre-wrap";
  const packagePlans = element("section", undefined, dialog);
  const buttons = element("div", undefined, dialog);
  Object.assign(buttons.style, { display: "flex", flexWrap: "wrap", gap: "8px" });
  const controls = [];
  let installButton;
  let launcherButton;
  let startButton;
  let supported = false;
  let dependenciesReady = false;
  function busy(value) {
    for (const control of controls) control.disabled = value;
    if (!value) {
      launcherButton.disabled = !supported;
      startButton.disabled = !supported;
      installButton.disabled = dependenciesReady;
    }
  }
  function action(title, callback) {
    const button = element("button", title, buttons);
    button.type = "button";
    button.onclick = async () => {
      busy(true);
      message.textContent = "Working…";
      try { await callback(); } catch (error) { message.textContent = error.message; }
      finally { if (!installing) busy(false); }
    };
    controls.push(button);
    return button;
  }
  function displayStatus(state, populate = false) {
    supported = state.automatic_lifecycle;
    dependenciesReady = state.dependencies.ready;
    detection.textContent = `ComfyUI: ${state.comfy_root}\nComfyUI Python: ${state.comfy_python}\nMCP dependencies: ${state.dependencies.ready ? "ready" : "setup required"}\n${state.lifecycle_message}`;
    tunnel.textContent = state.tailscale.message;
    available.textContent = `Installed folders: ${state.node_packs.join(", ") || "none detected"}. You can also name a new pack to create.`;
    if (populate) {
      python.value = state.python.executable;
      publicUrl.value = state.public_url || state.tailscale.public_url;
      packs.value = state.allowed_node_packs.join("\n");
      for (const [scope, input] of scopeInputs) input.checked = state.enabled_scopes.includes(scope);
    }
    installButton.textContent = state.dependencies.ready ? "Dependencies ready" : "2. Install MCP dependencies";
    packagePlans.replaceChildren();
    if (state.pending_package_plans?.length) {
      element("h3", "Review critical package changes", packagePlans);
      element("p", "These changes affect ComfyUI's Python environment. Review the exact versions before allowing the AI to install them.", packagePlans);
      for (const plan of state.pending_package_plans) {
        const entry = element("div", undefined, packagePlans);
        element("pre", plan.protected_changes.join("\n"), entry);
        const approve = element("button", "Approve these exact versions", entry);
        approve.type = "button";
        approve.onclick = async () => {
          approve.disabled = true;
          try {
            message.textContent = (await post("approve-package", { plan_id: plan.plan_id })).message;
            displayStatus(await post("status"));
          } catch (error) { message.textContent = error.message; approve.disabled = false; }
        };
      }
    }
    return state;
  }
  action("1. Save settings", async () => {
    const result = await post("save", {
      python: python.value.trim(), public_url: publicUrl.value.trim(),
      enabled_scopes: [...scopeInputs].filter(([, input]) => input.checked).map(([scope]) => scope),
      allowed_node_packs: packs.value.split(/[\n,]/).map((value) => value.trim()).filter(Boolean),
    });
    message.textContent = result.message;
    displayStatus(await post("status"));
  });
  installButton = action("2. Install MCP dependencies", async () => {
    const result = await post("install-dependencies");
    message.textContent = result.message;
    installing = true;
    const poll = async () => {
      try {
        const state = displayStatus(await post("status"));
        message.textContent = state.installation.message;
        if (state.installation.state === "running") { setTimeout(poll, 3000); return; }
      } catch (error) { message.textContent = `${error.message} Reopen setup to check installation progress.`; }
      installing = false;
      busy(false);
    };
    setTimeout(poll, 1500);
  });
  launcherButton = action("3. Link portable launchers", async () => { message.textContent = (await post("install-launchers")).message; });
  startButton = action("4. Start connection", async () => { message.textContent = (await post("start")).message; });
  const revealed = element("input", undefined, dialog);
  revealed.type = "text";
  revealed.readOnly = true;
  revealed.hidden = true;
  revealed.setAttribute("aria-label", "Private AI connection URL");
  Object.assign(revealed.style, { width: "100%", boxSizing: "border-box", marginTop: "12px", padding: "8px" });
  action("Show / copy connection URL", async () => {
    const value = await post("connection-url");
    revealed.value = value.url;
    revealed.hidden = false;
    try {
      await navigator.clipboard.writeText(value.url);
      message.textContent = `Private URL copied. Add it to your AI connector with Authentication: ${value.authentication}.`;
    } catch {
      revealed.focus(); revealed.select();
      message.textContent = `Copy the selected private URL. Authentication: ${value.authentication}.`;
    }
  });
  const close = element("button", "Close", buttons);
  close.type = "button";
  close.onclick = () => dialog.close();
  dialog.addEventListener("close", () => { revealed.value = ""; revealed.hidden = true; });
  dialog.refreshSetup = async () => {
    busy(true);
    try {
      const state = displayStatus(await post("status"), true);
      if (state.installation.state === "running") message.textContent = state.installation.message;
    } catch (error) { message.textContent = error.message; }
    finally { busy(false); }
  };
  dialog.showModal();
  await dialog.refreshSetup();
}

app.registerExtension({
  name: "arkennemasis.mcp.setup",
  setup() {
    const button = element("button", "MCP setup", document.body);
    button.type = "button";
    button.title = "Set up and manage your Arkennemasis AI connection";
    Object.assign(button.style, { position: "fixed", bottom: "12px", right: "16px", zIndex: "1000", padding: "8px 12px" });
    button.onclick = () => { void openSetup(); };
  },
});
