# ChatGPT / Codex & API Providers Setup Guide

Generate `gpt-image-2` images and run GPT-5 text + vision inside ComfyUI without paying per-token API costs.

---

## 1. Zero-Cost Image Gen via ChatGPT Plus

If you have an active ChatGPT Plus subscription ($20/month), you can generate `gpt-image-2` images directly in ComfyUI:

### Setup Steps:
1. Open a regular PowerShell terminal (not inside ComfyUI).
2. Run:
   ```sh
   codex login
   ```
3. Complete the web authentication prompt.
4. Your credentials will be saved in `~/.codex/auth.json`.
5. ComfyUI's **`arkennemasis Codex Image Gen`** and **`arkennemasis Codex LLM`** nodes will automatically detect and authenticate with your ChatGPT account.
6. **No API key is required**, and generations bill against your existing monthly plan instead of per-image API charges.

---

## 2. Checking Login Status in ComfyUI

Add the **`arkennemasis Codex Login Status`** node to any canvas. It outputs:
* The signed-in email account.
* Active plan level (Plus, Team, Pro).
* Session token expiration time.

---

## 3. Fallback: Replicate API Key

If you do not have a ChatGPT Plus subscription or prefer standard per-call cloud API billing:

1. Obtain a Replicate API token from [replicate.com](https://replicate.com).
2. Set it in your environment:
   ```sh
   REPLICATE_API_TOKEN=r8_...
   ```
   Or place it in a `.env` file at the root of your ComfyUI portable directory.
3. Use the matching Replicate nodes:
   * **`arkennemasis Replicate Image Gen (GPT-Image-2)`**
   * **`arkennemasis Replicate LLM (GPT-5)`**
4. The inputs and outputs map identically between Codex and Replicate nodes, allowing you to swap nodes seamlessly.

---

## 4. Shared Image Settings (`arkennemasis Image Gen Settings`)

Instead of configuring aspect ratio, quality, moderation, and background settings on every single image generation node:
1. Place one **`arkennemasis Image Gen Settings`** node on your canvas.
2. Wire its `ARK_IMAGE_SETTINGS` output into all downstream image generation nodes.
3. Changing quality or aspect ratio on this one node updates your entire pipeline at once.
