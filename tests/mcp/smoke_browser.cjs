// Optional rendered check; uses a fresh browser context and never edits/runs a workflow.
const { chromium } = require("playwright");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("http://127.0.0.1:8188/", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "MCP setup", exact: true }).click({ timeout: 45000 });
    const dialog = page.getByRole("dialog", { name: "Arkennemasis AI connection" });
    await dialog.waitFor({ state: "visible" });
    await page.getByRole("button", { name: /Save settings/i }).waitFor({ state: "visible" });
    await page.screenshot({ path: path.resolve(__dirname, "../../mcp_service/.local/setup-preview.png") });
    console.log(JSON.stringify({ setup_visible: true, controls: await dialog.getByRole("button").allTextContents(),
                                page_error_count: errors.length }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
