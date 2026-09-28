import { api } from "../../../scripts/api.js";

const PREFIX = "/arkennemasis/mcp/oauth";
let dialog = null;

function element(tag, text, parent) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  parent?.append(node);
  return node;
}

async function post(action, body = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await api.fetchApi(`${PREFIX}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Ark-Canvas": "1" },
      body: JSON.stringify(body), signal: controller.signal,
    });
    const text = await response.text();
    let value;
    try { value = JSON.parse(text); } catch { throw new Error(text.slice(0, 240) || "OAuth setup did not respond."); }
    if (!response.ok) throw new Error(value.error?.message || "OAuth setup could not complete.");
    return value;
  } finally { clearTimeout(timeout); }
}

export async function openOAuthSetup() {
  if (dialog) { dialog.showModal(); await dialog.refreshOAuth(); return; }
  dialog = element("dialog", undefined, document.body);
  Object.assign(dialog.style, {
    width: "min(620px, 90vw)", maxHeight: "85vh", overflowY: "auto", padding: "24px",
    background: "var(--comfy-menu-bg, #222)", color: "var(--input-text, #eee)",
    border: "1px solid var(--border-color, #555)", borderRadius: "12px", font: "14px system-ui, sans-serif",
  });
  const heading = element("h2", "OAuth sign-in", dialog);
  heading.id = "ark-mcp-oauth-heading";
  heading.style.marginTop = "0";
  dialog.setAttribute("aria-labelledby", heading.id);
  element("p", "Connect an external identity provider to your AI connection. The provider handles sign-in and consent. Configure it before enabling OAuth here.", dialog);
  const guide = element("a", "Provider setup guide", dialog);
  guide.href = "https://modelcontextprotocol.io/specification/latest/basic/authorization";
  guide.target = "_blank";
  guide.rel = "noopener noreferrer";
  const status = element("p", "Loading OAuth settings…", dialog);
  status.setAttribute("role", "status");
  status.style.whiteSpace = "pre-wrap";
  const form = element("form", undefined, dialog);
  form.onsubmit = (event) => event.preventDefault();
  const inputs = {};
  const field = (name, title, placeholder, multiple = false) => {
    const label = element("label", title, form);
    Object.assign(label.style, { display: "block", marginTop: "12px" });
    const input = element(multiple ? "textarea" : "input", undefined, label);
    if (!multiple) input.type = "text";
    else input.rows = 2;
    input.spellcheck = false;
    input.autocomplete = "off";
    input.placeholder = placeholder;
    Object.assign(input.style, { width: "100%", boxSizing: "border-box", display: "block", padding: "8px", marginTop: "5px" });
    inputs[name] = input;
    return input;
  };
  field("public_url", "Public HTTPS address", "https://your-computer.your-tailnet.ts.net");
  field("issuer_url", "Provider issuer (copy exactly, including any trailing slash)", "https://identity.example.com/");
  field("jwks_url", "Provider signing-keys URL (JWKS)", "https://identity.example.com/.well-known/jwks.json");
  field("audience", "Access-token audience / resource", "https://your-computer.your-tailnet.ts.net/mcp").readOnly = true;
  field("allowed_subjects", "Allowed user IDs (provider's sub values, one per line)", "Your exact provider user ID", true);
  field("allowed_algorithms", "Token signing algorithms (one per line)", "RS256", true);
  field("scope_claim", "Token scope claim", "scope");
  inputs.public_url.oninput = () => {
    const origin = inputs.public_url.value.trim().replace(/\/+$/, "");
    inputs.audience.value = origin ? `${origin}/mcp` : "";
  };
  const permissions = element("p", "", form);
  Object.assign(permissions.style, { fontSize: "12px", overflowWrap: "anywhere" });
  element("p", "Saving makes a draft. Enabling changes the saved connection mode; restart the AI connection to apply it. The old private URL will then stop accepting requests.", form);
  const buttons = element("div", undefined, dialog);
  Object.assign(buttons.style, { display: "flex", flexWrap: "wrap", gap: "8px" });
  const controls = [];
  let current = null;
  let dirty = false;
  let enableButton;
  let disableButton;
  const busy = (value) => {
    for (const control of controls) control.disabled = value;
    if (!value) {
      enableButton.disabled = dirty || !current?.draft_revision || Boolean(current?.errors?.length);
      disableButton.disabled = current?.auth_mode !== "oauth" || !current?.fallback_available;
    }
  };
  const action = (title, callback, parent = buttons) => {
    const button = element("button", title, parent);
    button.type = "button";
    controls.push(button);
    button.onclick = async () => {
      busy(true);
      try { await callback(); }
      catch (error) { status.textContent = error.name === "AbortError" ? "OAuth setup timed out. Refresh its status before retrying." : error.message; }
      finally { busy(false); }
    };
    return button;
  };
  const revoked = element("section", undefined, dialog);
  let clientInput;
  const show = (state, fill = false) => {
    current = state;
    status.textContent = [state.message, ...(state.errors || [])].filter(Boolean).join("\n");
    if (fill) {
      dirty = false;
      const provider = Object.keys(state.draft || {}).length ? state.draft : state.provider || {};
      for (const [name, input] of Object.entries(inputs)) {
        const value = provider[name];
        input.value = Array.isArray(value) ? value.join("\n") : value || (name === "scope_claim" ? "scope" : name === "allowed_algorithms" ? "RS256" : "");
      }
      inputs.public_url.oninput();
    }
    permissions.textContent = `Installation permissions: ${(state.enabled_scopes || []).join(", ") || "none"}. Tokens also need the scope for each requested operation.`;
    for (let index = controls.length - 1; index >= 0; index--) {
      if (revoked.contains(controls[index])) controls.splice(index, 1);
    }
    revoked.replaceChildren();
    element("h3", "Block an OAuth client", revoked);
    element("p", "Use the exact client_id or azp from your provider. Blocking takes effect on the next request, including for existing unexpired tokens. It does not change your provider's grants.", revoked);
    const label = element("label", "OAuth client ID", revoked);
    clientInput = element("input", undefined, label);
    clientInput.type = "text";
    clientInput.autocomplete = "off";
    Object.assign(clientInput.style, { display: "block", width: "100%", boxSizing: "border-box", padding: "8px", margin: "5px 0" });
    action("Block client", async () => {
      const result = await post("revoke", { client_id: clientInput.value.trim() });
      show(await post("status"));
      status.textContent = result.message;
    }, revoked);
    for (const identifier of state.revoked_clients || []) {
      const row = element("div", undefined, revoked);
      Object.assign(row.style, { marginTop: "8px", overflowWrap: "anywhere" });
      element("span", `${identifier} `, row);
      action("Unblock", async () => {
        const result = await post("unrevoke", { client_id: identifier });
        show(await post("status"));
        status.textContent = result.message;
      }, row);
    }
  };
  const providerValues = () => Object.fromEntries(Object.entries(inputs).map(([name, input]) => [
    name, ["allowed_subjects", "allowed_algorithms"].includes(name)
      ? input.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean) : input.value.trim(),
  ]));
  action("Save provider draft", async () => {
    const state = await post("save", { provider: providerValues(), config_revision: current?.config_revision });
    show(state, true);
  });
  enableButton = action("Enable OAuth after restart", async () => {
    show(await post("enable", { config_revision: current?.config_revision, draft_revision: current?.draft_revision }), true);
  });
  disableButton = action("Restore private-URL mode", async () => {
    show(await post("disable", { config_revision: current?.config_revision }), true);
  });
  form.addEventListener("input", () => { dirty = true; enableButton.disabled = true; });
  action("Refresh status", async () => show(await post("status"), true));
  const close = element("button", "Close", buttons);
  close.type = "button";
  close.onclick = () => dialog.close();
  dialog.refreshOAuth = async () => {
    busy(true);
    try { show(await post("status"), true); }
    catch (error) { status.textContent = error.message; }
    finally { busy(false); }
  };
  dialog.showModal();
  await dialog.refreshOAuth();
}
