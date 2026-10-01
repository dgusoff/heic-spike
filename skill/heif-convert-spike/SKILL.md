---
name: heif-convert-spike
description: |
  Tests local full-resolution HEIF/HEIC recovery of manually attached images into verified PNGs, using metadata-defined tile extraction and FFmpeg reconstruction first. Use when asked to "try heif-convert-spike", "decode my attached HEIC locally", "test this HEIF upload", "recover the complete HEIC grid", or "convert this HEIC to PNG". This is a decoder proof of concept, not OCR. Do NOT use for pile-log extraction or processing — use pile-log-reader instead; do not search business files, install packages, use external plug-ins or cloud services.
metadata:
  category: custom
  icon: ImagePixel
---

## Overview
Recover the complete primary image from a manually attached `.heif`, `.heic` or `.hif`, preserving the source unchanged. Use the durable FFmpeg item-extraction helper first; Pillow is used for PNG assembly, not as a HEIF decoder. All generated candidates remain provisional until complete-image visual review. This does not modify pile-log-reader.

### Proven approach, bounded compatibility
A default FFmpeg conversion can emit a single tile while exiting successfully. That is incomplete primary recovery, not proof that FFmpeg cannot decode the file. An explicitly mapped additional stream may expose another tile, but stream indices do not establish the grid layout. The working path parses HEIF metadata, extracts every referenced `hvc1` item with its `hvcC` parameter sets, decodes each locally with FFmpeg, and assembles the `dimg` reference sequence in HEIF-defined row-major order. Only metadata-required excess at the right/bottom edges is cropped.

The regression fixture was a six-row, five-column grid: primary item 31; thirty 896×960 tiles; primary dimensions 4284×5712; excess 196 pixels right and 48 bottom; EXIF orientation 1 and identity `irot`. Reopened full PNG regions matched every decoded tile pixel-for-pixel, and a whole preview plus four exhaustive full-resolution quadrants showed continuous upright content. These are historical facts for one file, **not defaults for another attachment**. The helper derives every dimension, item ID and layout from each source.

The [regression fixture record](references/regression-fixture.json) contains only observed metadata and hashes, not image bytes. [scripts/test_recovery.py](scripts/test_recovery.py) checks malformed-input boundaries offline; `--fixture <explicitly-supplied-original> --working-dir working/heif-regression` additionally rechecks that specific original in temporary scratch. It does not search for input or replace visual review.

The primary helper deliberately supports only identity-oriented HEVC grid primaries with uniform tiles, bounded item extents, supported essential properties, declared 8-bit three-channel pixels when present, and an embedded primary ICC profile. It fails closed on unsupported constructions, transforms, dependencies, decoder errors or missing tiles. It renders 8-bit RGB and preserves the original ICC bytes; it does not recover auxiliary/depth/alpha images, animation, HDR semantics or all original metadata. No decoder binaries are bundled.

## When to Use
- A user manually attaches HEIF/HEIC and requests this local conversion spike.
- A user asks whether the entire primary grid can be recovered, rather than a tile or thumbnail.

## When NOT to Use
- OCR, interpreting handwritten tables or pile-log automation — use pile-log-reader instead.
- Teams, OneDrive, SharePoint or business-file searches; only attachments and this skill's supporting assets are in scope.
- Installation, downloads, external plug-ins/cloud conversion, broad runtime inventory or security-control workarounds.
- General media conversion, unsupported features, or sessions that prohibit the required local execution. Stop and offer a manually uploaded JPG.

## Quick Start
User: "Recover the complete HEIC grid from my attachment."
1. Resolve the supplied attachment and check session permission for local execution.
2. Use Bash to run the primary helper from the installed skill directory:
   ```
   python <skill-dir>/scripts/convert_heif.py <attachment-path> --output-dir working/heif-trial-001
   ```
3. Read the manifest, review the reopened whole PNG and exhaustive details with `view`, and publish only after all gates pass.

Use the tool discovery interface to retrieve artifact-tool schemas before invoking `CopyArtifact` or `RenderArtifact`; use the Bash and `view` tools for local execution and image review.

## Workflow
1. **Input and permission gate.** Use only attachment handles/paths supplied in the conversation. If no image is attached, ask for one and stop; do not fabricate a trial or search business storage. If local execution or binary loading is prohibited, stop. Preserve original bytes. No package managers, downloads, runtime probes or environment inventory.
2. **Primary local recovery.** Run [scripts/convert_heif.py](scripts/convert_heif.py) using Bash/approved Python, with a fresh nonexistent output directory under `working/`. Pillow plus an already available FFmpeg are needed; the helper discovers FFmpeg through `imageio_ffmpeg.get_ffmpeg_exe()` or executable lookup, not a hardcoded environment path. Normal dependency import/lookup is not permission for runtime inspection. An optional `--expected-primary-size WIDTH HEIGHT` checks an independently supplied expectation; never guess it.
3. **Check authoritative metadata and reconstruction.** Read `recovery-manifest.json`. Require `candidates_ready`, explicit primary identity, grid dimensions agreeing with `ispe`, exactly rows×columns referenced/decoded tiles, metadata-defined row-major order, consistent tile sizes, correct coverage and documented right/bottom edge crop. Require reopened RGB PNG dimensions, ICC bytes and all visible tile regions to match. The manifest records source/output SHA-256, decoder method, tile-to-source mapping, orientation evidence and unattempted auxiliary recovery. Exit zero, output existence or a single default FFmpeg frame is never sufficient. Unsupported transforms/structures, malformed bounds, incomplete tiles or decoder errors are terminal for that path; do not manually guess a repair.
4. **Complete-image visual gate.** The primary helper always returns `visual_verification: pending`, not a success assertion. Use `view` on the actual reopened primary PNG or an image-capable `RenderArtifact` after discovering its schema. Review the whole image for scene/page extent, orientation, missing/blank tiles and continuity. If the viewer cannot show the full-resolution PNG because of size, review `full-preview.png` **and all four exhaustive full-resolution quadrant PNGs**, which the helper derives from the reopened final PNG; their recorded bounds cover the entire image. A preview alone or a few selected crops are insufficient. Do not OCR. If viewing fails or completeness remains uncertain, report the blocker and offer JPG. Record actual visual findings in the manifest only after inspection.
5. **Deliver verified results.** After both metadata/pixel and visual gates pass, discover and call `CopyArtifact(surface="output", source="working/...", destination="<source>-primary-full.png")`. Verify returned success and copied count before confirming delivery. Finish writing before copying; retry the same call after a source-visibility delay, never write output directly. Record final published path and visual findings. Do not publish sources/private metadata unless requested.

## Success / Failure Criteria
**Verified and delivered:** the actual attachment was decoded locally; the full primary identity, dimensions, ordering and supported orientation are established by source metadata; reopened PNG and every visible tile region pass; the whole scene and exhaustive details are visually reviewed; artifact publication succeeds.

**Blocked / failed:** no supplied input, execution prohibited, no available decoder, unsupported metadata/features, invalid bounds, resource limit, ambiguous primary, incomplete tiles, dimension mismatch, decode errors, uncertain visual review or failed publication. Keep candidates provisional and give the precise blocker with a JPG fallback. Never call a partial image a full recovery, or generalize one fixture's success to all HEIC files.

## Output Format
Reply briefly in four bullet points:
- **Status:** Awaiting upload / Blocked / Failed / Verified and delivered.
- **Source → output:** actual filename and hash, primary item/frame identity, final link only after successful publication.
- **Checks:** metadata/observed dimensions, tile count/order/edge crop, orientation evidence, actual visual findings and decoder.
- **Limitations:** 8-bit RGB rendering with original ICC; no claim of HDR, auxiliary or archival metadata recovery. If not tested, say so.

## Guardrails
- Never transmit input to external services, use cloud conversion/plug-ins, or search business sources.
- Never install/download packages, inventory the runtime or load dependencies against session restrictions.
- Always keep sources unchanged and intermediates under `working/`; refuse existing output directories.
- Always treat metadata as untrusted: enforce box/extent/index/pixel/count limits, reject unsupported transforms and essential properties, require complete tiles. FFmpeg runs with argument vectors, no shell, file-only protocol, one frame, single threads and time limits.
- Never guess dimensions, item order, orientation, image contents or delivery. Source metadata and actual checks outrank convenience.
- Do not change pile-log-reader; conversion remains a manually attached-image spike, not an OCR workflow.
