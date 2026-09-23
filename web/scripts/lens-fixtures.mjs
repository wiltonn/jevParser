// Regenerate src/lib/lens.fixtures.json: lens values and bands computed by the
// Python implementation over the sample comparison, so lens.test.ts can prove
// the TypeScript port agrees with it.
import { execFileSync } from "node:child_process";

execFileSync("uv", ["run", "python", "web/scripts/lens_fixtures.py"], {
  cwd: new URL("../..", import.meta.url),
  stdio: "inherit",
});
