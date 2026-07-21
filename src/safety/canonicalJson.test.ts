import assert from "node:assert/strict";
import test from "node:test";
import { canonicalJson, sha256Hex } from "./canonicalJson";

test("canonicalJson sorts object keys and preserves array order", () => {
  assert.equal(
    canonicalJson({ z: 1, a: { d: 4, b: 2 }, list: [{ y: 2, x: 1 }] }),
    '{"a":{"b":2,"d":4},"list":[{"x":1,"y":2}],"z":1}',
  );
});

test("sha256Hex is stable for equivalent objects", async () => {
  const left = await sha256Hex({ b: 2, a: 1 });
  const right = await sha256Hex({ a: 1, b: 2 });
  assert.equal(left, right);
});
