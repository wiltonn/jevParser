// Regenerate src/design/tokens.gen.css from the design system's tokens.json,
// through the same Python function the HTML report uses.  A Python test fails
// if the checked-in file drifts.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

const css = execFileSync(
  "uv",
  ["run", "python", "-c", "from jev_diff.render import tokens; print(tokens.css(), end='')"],
  { cwd: new URL("../..", import.meta.url), encoding: "utf8" },
);
writeFileSync(new URL("../src/design/tokens.gen.css", import.meta.url), css);
console.log("wrote src/design/tokens.gen.css");
