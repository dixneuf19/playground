// Run by deno (make check-format): npm: imports, so no package.json or node_modules.
import js from "npm:@eslint/js@10.0.1";
import globals from "npm:globals@17.14.0";

const common = ["connectShow", "esc", "TEAMS", "teamIcon", "formatTimer", "stored", "store"];

export default [
  js.configs.recommended,
  {
    files: ["coco_lanta/static/*.js"],
    languageOptions: {
      sourceType: "script",
      // LiveKit's client comes from jsDelivr (templates/_livekit.html).
      globals: { ...globals.browser, LivekitClient: "readonly" },
    },
  },
  {
    // Loaded after common.js.
    files: ["coco_lanta/static/!(common).js"],
    languageOptions: {
      globals: Object.fromEntries(common.map((name) => [name, "readonly"])),
    },
  },
];
