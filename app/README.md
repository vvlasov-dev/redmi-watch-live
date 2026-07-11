# app/ — frontend shell

`shell.html` = head + theme + the render harness + shared chart builders. The
server composes it with each feature's `panel.html`. See `docs/MIGRATION.md`
(frontend plan — the renderVals split is a refactor, browser-verify each step).
