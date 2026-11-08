// Run by deno (make check-format): npm: imports, so no package.json or node_modules.
import js from "npm:@eslint/js@10.0.1";
import globals from "npm:globals@17.14.0";

export default [
  js.configs.recommended,
  {
    files: ["karaoke/static/*.js"],
    languageOptions: {
      sourceType: "script",
      globals: globals.browser,
    },
  },
  {
    // Loaded after common.js in display.html.
    files: ["karaoke/static/display.js"],
    languageOptions: {
      globals: { ROOM_CODE: "readonly", connectRoom: "readonly", esc: "readonly" },
    },
  },
  {
    // htmx and SortableJS come from jsDelivr in guest.html.
    files: ["karaoke/static/guest.js"],
    languageOptions: {
      globals: { htmx: "readonly", Sortable: "readonly" },
    },
  },
];
