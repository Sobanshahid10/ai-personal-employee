#!/usr/bin/env node

/** ChiefMind Gmail send tool over MCP stdio. */

import { readFile, rename, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { google } from "googleapis";
import { z } from "zod";


const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(MODULE_DIR, "../..");
const MAX_BODY_LENGTH = 100_000;
const MAX_SUBJECT_LENGTH = 998;

function configuredPath(variable, fallback) {
  const value = process.env[variable]?.trim();
  if (!value) return fallback;
  return isAbsolute(value) ? value : resolve(process.cwd(), value);
}

export const CREDENTIALS_FILE = configuredPath(
  "GOOGLE_CREDENTIALS_FILE",
  resolve(PROJECT_ROOT, "credentials/credentials.json"),
);
export const TOKEN_FILE = configuredPath(
  "GOOGLE_TOKEN_FILE",
  resolve(PROJECT_ROOT, "credentials/token.json"),
);

export function validateEmail(value) {
  if (/[\r\n]/u.test(value)) throw new Error("Recipient must not contain newlines.");
  // MCP accepts one addr-spec. Add CC/BCC as separate reviewed tools later.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(value)) {
    throw new Error("Recipient must be one valid email address.");
  }
  return value;
}

export function buildRawMessage({ from, to, subject, body }) {
  validateEmail(from);
  validateEmail(to);
  if (/[\r\n]/u.test(subject)) throw new Error("Subject must not contain newlines.");
  if (subject.length > MAX_SUBJECT_LENGTH) throw new Error("Subject is too long.");
  if (body.length > MAX_BODY_LENGTH) throw new Error("Body is too long.");

  // RFC 2047 protects non-ASCII subjects; base64 transfer encoding preserves
  // the exact UTF-8 body and avoids accidental MIME boundary problems.
  const encodedSubject = `=?UTF-8?B?${Buffer.from(subject, "utf8").toString("base64")}?=`;
  const encodedBody = Buffer.from(body, "utf8").toString("base64");
  const lines = [
    `From: ${from}`,
    `To: ${to}`,
    `Subject: ${encodedSubject}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    encodedBody,
  ];
  return Buffer.from(lines.join("\r\n"), "utf8").toString("base64url");
}

async function readJson(path, label) {
  let text;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    throw new Error(`Could not read ${label} at ${path}: ${error.message}`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${error.message}`);
  }
}

async function persistRefreshedToken(original, tokens) {
  const updated = {
    ...original,
    ...(tokens.access_token ? { token: tokens.access_token } : {}),
    ...(tokens.refresh_token ? { refresh_token: tokens.refresh_token } : {}),
    ...(tokens.scope ? { scopes: tokens.scope.split(" ") } : {}),
    ...(tokens.expiry_date ? { expiry: new Date(tokens.expiry_date).toISOString() } : {}),
  };
  const temporary = `${TOKEN_FILE}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(updated, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await rename(temporary, TOKEN_FILE);
}

export async function createGmailClient() {
  const [clientDocument, tokenDocument] = await Promise.all([
    readJson(CREDENTIALS_FILE, "credentials.json"),
    readJson(TOKEN_FILE, "token.json"),
  ]);
  const client = clientDocument.installed ?? clientDocument.web;
  if (!client?.client_id || !client?.client_secret) {
    throw new Error("credentials.json must contain an installed or web OAuth client.");
  }
  if (!tokenDocument.refresh_token) {
    throw new Error("token.json has no refresh_token; authenticate Gmail again.");
  }

  const auth = new google.auth.OAuth2(
    tokenDocument.client_id ?? client.client_id,
    tokenDocument.client_secret ?? client.client_secret,
    client.redirect_uris?.[0],
  );
  auth.setCredentials({
    access_token: tokenDocument.token ?? tokenDocument.access_token,
    refresh_token: tokenDocument.refresh_token,
    scope: Array.isArray(tokenDocument.scopes)
      ? tokenDocument.scopes.join(" ")
      : tokenDocument.scope,
    token_type: tokenDocument.token_type ?? "Bearer",
    expiry_date: tokenDocument.expiry ? Date.parse(tokenDocument.expiry) : undefined,
  });
  auth.on("tokens", (tokens) => {
    persistRefreshedToken(tokenDocument, tokens).catch((error) => {
      console.error(`Could not persist refreshed Gmail token: ${error.message}`);
    });
  });
  return google.gmail({ version: "v1", auth });
}

export async function sendEmail({ to, subject, body }, gmailFactory = createGmailClient) {
  if (process.env.MCP_GMAIL_SEND_ENABLED !== "true") {
    throw new Error(
      "Gmail sending is disabled. Set MCP_GMAIL_SEND_ENABLED=true after reviewing the recipient and content.",
    );
  }
  validateEmail(to);
  const gmail = await gmailFactory();
  const profile = await gmail.users.getProfile({ userId: "me" });
  const from = profile.data.emailAddress;
  if (!from) throw new Error("Gmail profile did not return an email address.");
  const raw = buildRawMessage({ from, to, subject, body });
  const response = await gmail.users.messages.send({
    userId: "me",
    requestBody: { raw },
  });
  if (!response.data.id) throw new Error("Gmail accepted the request without a message ID.");
  return {
    success: true,
    messageId: response.data.id,
    threadId: response.data.threadId ?? null,
  };
}

export function buildServer() {
  const server = new McpServer(
    { name: "chiefmind-gmail-send", version: "1.0.0" },
    {
      instructions:
        "Use send_email only when the user explicitly authorizes the exact recipient, subject, and body.",
    },
  );
  server.registerTool(
    "send_email",
    {
      title: "Send Gmail Email",
      description:
        "Send one exact plain-text email through the authenticated Gmail account. This has an external side effect.",
      inputSchema: {
        to: z.string().email().describe("One recipient email address"),
        subject: z.string().max(MAX_SUBJECT_LENGTH).describe("Exact email subject"),
        body: z.string().max(MAX_BODY_LENGTH).describe("Exact plain-text email body"),
      },
      outputSchema: {
        success: z.boolean(),
        messageId: z.string(),
        threadId: z.string().nullable(),
      },
      annotations: {
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async (input) => {
      try {
        const result = await sendEmail(input);
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
          structuredContent: result,
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`send_email failed: ${message}`);
        return {
          isError: true,
          content: [{ type: "text", text: JSON.stringify({ success: false, error: message }) }],
        };
      }
    },
  );
  return server;
}

export async function main() {
  const server = buildServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // stdout belongs exclusively to MCP frames; diagnostics must use stderr.
  console.error("ChiefMind Gmail MCP server ready on stdio.");
}

const invokedDirectly = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  main().catch((error) => {
    console.error(`Fatal MCP server error: ${error.stack ?? error}`);
    process.exitCode = 1;
  });
}
