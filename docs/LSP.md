# Ohm LSP — editor integration

`ohm lsp` is a stdlib-only Language Server over stdio. It surfaces the full
checker pipeline (parse, resolve, units, Tier-S, Tier-I, closed-form, Tier-Q)
as `publishDiagnostics` on open/change/save — a swapped SDA/SCL is a red
squiggle before any build.

## VS Code

VS Code has no built-in generic LSP client, so either:

1. **Generic LSP client extension** — install any marketplace extension that
   launches an arbitrary language server, and point it at command `ohm` with
   args `["lsp"]` over stdio for files matching `*.ohm`. Each such extension
   names its settings differently — consult its README for the exact keys.

2. **Minimal own extension** — a skeleton using the official
   `vscode-languageclient` library; in `activate()`:

   ```js
   const { LanguageClient } = require("vscode-languageclient/node");
   const client = new LanguageClient(
     "ohm", "Ohm LSP",
     { command: "ohm", args: ["lsp"] },  // stdio is the default transport
     { documentSelector: [{ scheme: "file", pattern: "**/*.ohm" }] },
   );
   client.start();
   ```

   plus a `package.json` that contributes a language id `ohm` for the `.ohm`
   file extension and activates on it.

## Neovim (built-in LSP)

```lua
vim.api.nvim_create_autocmd("FileType", {
  pattern = "ohm",
  callback = function()
    vim.lsp.start({ name = "ohm", cmd = { "ohm", "lsp" } })
  end,
})
vim.filetype.add({ extension = { ohm = "ohm" } })
```

Diagnostics carry the stable code (e.g. `I005`); run `ohm explain I005` for the
documented cause and fix.
