# Ohm LSP — editor integration

`ohm lsp` is a stdlib-only Language Server over stdio. It surfaces the full
checker pipeline (parse, resolve, units, Tier-S, Tier-I, closed-form, Tier-Q)
as `publishDiagnostics` on open/change/save — a swapped SDA/SCL is a red
squiggle before any build.

## VS Code

With any generic LSP client extension (e.g. "LSP Client" / `vscode-languageclient`
based), configure:

```jsonc
{
  "languageServerExample.command": "ohm",
  "languageServerExample.args": ["lsp"],
  "files.associations": { "*.ohm": "ohm" }
}
```

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
