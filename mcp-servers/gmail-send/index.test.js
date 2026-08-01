import assert from "node:assert/strict";
import test from "node:test";

import { buildRawMessage, sendEmail, validateEmail } from "./index.js";


test("buildRawMessage creates a base64url RFC message with exact body", () => {
  const raw = buildRawMessage({
    from: "sender@example.com",
    to: "recipient@example.com",
    subject: "Hello ✓",
    body: "Exact body\nsecond line",
  });
  const mime = Buffer.from(raw, "base64url").toString("utf8");
  assert.match(mime, /To: recipient@example\.com/);
  assert.match(mime, /Content-Transfer-Encoding: base64/);
  const encodedBody = mime.split("\r\n\r\n")[1];
  assert.equal(Buffer.from(encodedBody, "base64").toString("utf8"), "Exact body\nsecond line");
});

test("header injection is rejected", () => {
  assert.throws(() => validateEmail("safe@example.com\nBcc: evil@example.com"));
});

test("send is fail-closed unless explicitly enabled", async () => {
  const previous = process.env.MCP_GMAIL_SEND_ENABLED;
  delete process.env.MCP_GMAIL_SEND_ENABLED;
  try {
    await assert.rejects(
      sendEmail({ to: "person@example.com", subject: "Test", body: "Body" }),
      /disabled/,
    );
  } finally {
    if (previous === undefined) delete process.env.MCP_GMAIL_SEND_ENABLED;
    else process.env.MCP_GMAIL_SEND_ENABLED = previous;
  }
});
