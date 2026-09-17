# Product Variation Pipeline — Architecture & Setup Guide

A client's variation spreadsheet plus one locked base photograph in; a verified, consistently framed image library out.

Any product, any number of variation axes. The guarantee is that **every delivered image shows the exact same physical object, differing only in the specified attribute**.

---

## 1. Pipeline Overview (35 Nodes)

The pipeline is organized into **ten modular stages**:

| Stage | Node(s) | Function |
| :--- | :--- | :--- |
| **1. Intake** | `Sheet Probe`, `Variation Intake` | Reads client sheet (CSV/TSV/JSON), normalizes into `VARIANTS` / `SPECS` / `PRODUCT`, and validates inputs. Fails loudly on missing data rather than guessing. |
| **2. References** | `Spec Library` | Downloads, caches, and content-hashes reference images; renders swatches per hex code. Accumulates across products per client. |
| **3. Plate Lock** | `Plate Lock`, `Region Mask` | Freezes the base product photograph: measures hash, dimensions, colour profile, bounding boxes, and ratios. Resolves target masks. |
| **4. Recipe** | `Recipe Brief`, `Recipe Compile`, `Recipe Gate` | Discovers regions via Tier 0 meta-prompt, merges the library, and injects locks and tolerances as constants. The human gate guards `paints`. |
| **5. Cell Matrix** | `Cell Matrix`, `Cell At` | Computes the cartesian product of N axes × plates, selecting one cell by index. |
| **6. Prompts** | `Prompt Build`, `Prompt Audit` | Pure substitution prompt assembly (instruction + invariants + locks, byte-identical across the run). Verifies prompt constancy. |
| **7. Execution** | `Gen Route`, `Region Recolour` | Routes to deterministic recolouring (CIELAB) or generative rendering per axis. Unselected branches are lazy and never billed. |
| **8. Verification** | `Verify Candidate`, `Calibrate` | Automated quality control: measures product identity, frame match, colour ΔE2000, bleed, and hygiene (pass / soft / hard). |
| **9. Job Store** | `Job Skip`, `Job Record`, `Run Report` | Durable per-cell disk records. Lazy evaluation ensures finished cells are never re-generated or re-billed. |
| **10. Delivery** | `Deliver`, `Review Board`, `Store Export` | Exports format ladders (PNG / white JPG / WebP), generates an interactive HTML review board, and exports WooCommerce-ready CSVs. |

---

## 2. Specification Formats

A colour's specification format is a property of the **VALUE**, not the axis:

| Format | `spec_type` | Reference sent? | Colour auto-checked? |
| :--- | :--- | :--- | :--- |
| **Hex only** | `hex` | No | Yes |
| **Hex + description** | `hex` | No | Yes |
| **Reference image** | `reference_image` | Yes | No (no target swatch) |
| **Reference image + hex** | `reference_image` | Yes | Yes |

*Note:* A bare text word with neither hex nor image is rejected: a word is an opinion, and the model produces a different interpretation each time.

---

## 3. The Two Execution Paths

1. **Ten-Stage Pipeline:** Used when dealing with unstructured client sheets where prompts and region bounds must be dynamically compiled and verified.
2. **CSV Catalogue Path:** Shorter path (`Catalogue Load` / `Fan-Out` / `Save` / `Board`) used when prompts already exist and each row maps 1:1 to an image.
