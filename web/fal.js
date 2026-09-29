// arkennemasis fal nodes - their own look, so they never read as stock ComfyUI nodes.
//
// Every fal node gets a colour identity from its menu category:
//   Image -> magenta/amber   Video -> electric blue/violet   Audio -> green/cyan
//   Lip Sync -> orange/gold  Tools -> teal/blue
// (the TYPE is the menu level after "fal/"; the family - Flux, MiniMax... - is in the tag)
// applied as the node's own colours (works in the classic canvas AND the Vue "Nodes 2.0"
// renderer) plus, on the classic canvas, a gradient edge, a category tag and a card shape.
// The running animation (activity.js) picks up the same accent colour.
//
// Purely cosmetic: every drawing call is guarded, and nothing here touches widgets,
// inputs or values, so it can never change what a node sends.

import { app } from "../../scripts/app.js";

const PREFIX = "ArkFal";

const LOOKS = {
  Image: { title: "#6b1d52", body: "#241020", a: "#ff4fa3", b: "#ffb347", rgb: "255,79,163", tag: "IMAGE" },
  Video: { title: "#1a3a78", body: "#0d1729", a: "#3aa0ff", b: "#8a5cff", rgb: "58,160,255", tag: "VIDEO" },
  "Lip Sync": { title: "#743510", body: "#26150b", a: "#ff8a3d", b: "#ffd23d", rgb: "255,138,61", tag: "LIP SYNC" },
  Audio: { title: "#1f5a2c", body: "#0e2214", a: "#5dff8a", b: "#27d8ff", rgb: "93,255,138", tag: "AUDIO" },
  Tools: { title: "#14544b", body: "#0c211e", a: "#2ee6b8", b: "#3aa0ff", rgb: "46,230,184", tag: "TOOLS" },
};
const DEFAULT_LOOK = LOOKS.Tools;

function lookFor(nodeData) {
  // arkennemasis/fal/<Type>/<Family>
  const parts = String(nodeData?.category || "").split("/");
  const look = LOOKS[parts[2]] || DEFAULT_LOOK;
  const family = parts.length > 3 ? parts.slice(3).join(" ").toUpperCase() : "";
  return family ? { ...look, tag: look.tag + " · " + family } : look;
}

function isFal(name) {
  return typeof name === "string" && name.startsWith(PREFIX);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawLook(node, ctx, look) {
  if (node.flags?.collapsed) return;
  const W = node.size[0];
  const H = node.size[1];
  ctx.save();

  // a gradient seam right under the title
  const seam = ctx.createLinearGradient(0, 0, W, 0);
  seam.addColorStop(0, look.a);
  seam.addColorStop(1, look.b);
  ctx.fillStyle = seam;
  ctx.fillRect(0, 0, W, 2);

  // a glowing left edge
  const edge = ctx.createLinearGradient(0, 0, 0, H);
  edge.addColorStop(0, look.a);
  edge.addColorStop(1, look.b);
  ctx.globalAlpha = 0.85;
  ctx.fillStyle = edge;
  roundRect(ctx, 0, 0, 3, H, 1.5);
  ctx.fill();
  ctx.globalAlpha = 1;

  // a small "fal · CATEGORY" tag, bottom-right
  const text = "fal · " + look.tag;
  ctx.font = "bold 9px sans-serif";
  const tw = ctx.measureText(text).width;
  const px = W - tw - 16;
  const py = H - 16;
  if (px > 40 && py > 30) {
    const pill = ctx.createLinearGradient(px, 0, px + tw + 10, 0);
    pill.addColorStop(0, look.a);
    pill.addColorStop(1, look.b);
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = pill;
    roundRect(ctx, px, py, tw + 10, 12, 6);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#101010";
    ctx.textBaseline = "middle";
    ctx.fillText(text, px + 5, py + 6.5);
  }
  ctx.restore();
}

app.registerExtension({
  name: "arkennemasis.fal.look",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!isFal(nodeData?.name)) return;
    const look = lookFor(nodeData);
    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      try {
        this.color = look.title;
        this.bgcolor = look.body;
        this.__arkAccent = look.rgb;
        if (globalThis.LiteGraph?.CARD_SHAPE !== undefined) this.shape = globalThis.LiteGraph.CARD_SHAPE;
      } catch (e) {
        /* cosmetic only */
      }
      return r;
    };
    const origDraw = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      const r = origDraw?.apply(this, arguments);
      try {
        this.__arkAccent = this.__arkAccent || look.rgb;
        drawLook(this, ctx, look);
      } catch (e) {
        /* cosmetic only */
      }
      return r;
    };
  },
  // API-flagged nodes are painted yellow by the frontend when created; repaint ours after it.
  nodeCreated(node) {
    try {
      const type = node?.comfyClass || node?.type;
      if (!isFal(type)) return;
      const look = lookFor(node.constructor?.nodeData);
      node.color = look.title;
      node.bgcolor = look.body;
      node.__arkAccent = look.rgb;
    } catch (e) {
      /* cosmetic only */
    }
  },
});
