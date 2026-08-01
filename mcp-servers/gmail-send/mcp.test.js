import assert from "node:assert/strict";
import test from "node:test";
import { resolve } from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";


test("stdio server advertises exactly the send_email tool", async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [resolve("index.js")],
    env: { ...process.env, MCP_GMAIL_SEND_ENABLED: "false" },
  });
  const client = new Client({ name: "chiefmind-test", version: "1.0.0" });
  try {
    await client.connect(transport);
    const response = await client.listTools();
    assert.deepEqual(response.tools.map((tool) => tool.name), ["send_email"]);
    assert.deepEqual(
      Object.keys(response.tools[0].inputSchema.properties).sort(),
      ["body", "subject", "to"],
    );
  } finally {
    await client.close();
  }
});
