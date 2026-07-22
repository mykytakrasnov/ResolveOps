#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import process from "node:process";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
if (existsSync(path.join(repositoryRoot, ".env"))) {
  process.loadEnvFile(path.join(repositoryRoot, ".env"));
}

const postgresUser = process.env.POSTGRES_USER ?? "resolveops";
const postgresPassword = process.env.POSTGRES_PASSWORD ?? "resolveops";
const postgresDatabase = process.env.POSTGRES_DB ?? "resolveops";
const managesPostgres =
  process.env.DATABASE_URL_POOLED === undefined &&
  process.env.DATABASE_URL_DIRECT === undefined;
const postgresPort = managesPostgres
  ? await selectPostgresPort(process.env.POSTGRES_PORT)
  : (process.env.POSTGRES_PORT ?? "5432");
const defaultDatabaseUrl = `postgresql+psycopg://${encodeURIComponent(postgresUser)}:${encodeURIComponent(postgresPassword)}@127.0.0.1:${postgresPort}/${encodeURIComponent(postgresDatabase)}`;
const databaseUrlPooled =
  process.env.DATABASE_URL_POOLED ??
  process.env.DATABASE_URL_DIRECT ??
  defaultDatabaseUrl;
const databaseUrlDirect = process.env.DATABASE_URL_DIRECT ?? databaseUrlPooled;
const agentApiPort = process.env.RESOLVEOPS_AGENT_API_PORT ?? "8000";
const webPort = process.env.RESOLVEOPS_WEB_PORT ?? "5173";
const generatedRoot = path.join(repositoryRoot, "data/generated");
const pythonSource = path.join(repositoryRoot, "services/agent-api/src");

const environment = {
  ...process.env,
  DATABASE_URL_DIRECT: databaseUrlDirect,
  DATABASE_URL_POOLED: databaseUrlPooled,
  POSTGRES_PORT: postgresPort,
  PYTHONPATH: [pythonSource, process.env.PYTHONPATH]
    .filter(Boolean)
    .join(path.delimiter),
  RESOLVEOPS_AGENT_API_URL: `http://127.0.0.1:${agentApiPort}`,
  RESOLVEOPS_SYNTHETIC_DATA_ROOT: generatedRoot,
};

function portIsAvailable(port) {
  return new Promise((resolve) => {
    const server = createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen(
      { host: "0.0.0.0", port: Number(port), exclusive: true },
      () => {
        server.close(() => resolve(true));
      },
    );
  });
}

async function selectPostgresPort(configuredPort) {
  if (configuredPort !== undefined) return configuredPort;
  const existing = spawnSync(
    "docker",
    ["compose", "port", "postgres", "5432"],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
    },
  );
  const existingPort = existing.stdout?.trim().match(/:(\d+)$/)?.[1];
  if (existing.status === 0 && existingPort !== undefined) return existingPort;
  for (const candidate of ["5432", "55432", "55433", "55434", "55435"]) {
    if (await portIsAvailable(candidate)) {
      if (candidate !== "5432") {
        console.log(
          `[resolveops] PostgreSQL port 5432 is occupied; using ${candidate}`,
        );
      }
      return candidate;
    }
  }
  throw new Error(
    "No local PostgreSQL port is available; set POSTGRES_PORT explicitly.",
  );
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    env: environment,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} exited with status ${result.status}`,
    );
  }
}

function start(command, args) {
  return spawn(command, args, {
    cwd: repositoryRoot,
    env: environment,
    stdio: "inherit",
    detached: process.platform !== "win32",
  });
}

function terminate(child, signal = "SIGTERM") {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    child.kill(signal);
  } else if (child.pid !== undefined) {
    process.kill(-child.pid, signal);
  }
}

async function waitForApi(url, child) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(
        `Agent API exited before becoming ready (${child.exitCode}).`,
      );
    }
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(1_000),
      });
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Agent API did not become ready at ${url}.`);
}

let agentApi;
let web;
let stopping = false;

function stop(signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  if (web) terminate(web, signal);
  if (agentApi) terminate(agentApi, signal);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => stop(signal));
}

try {
  if (managesPostgres) {
    console.log("[resolveops] starting local PostgreSQL");
    run("docker", ["compose", "up", "-d", "--wait", "postgres"]);
  } else {
    console.log("[resolveops] using configured PostgreSQL connection");
  }

  console.log("[resolveops] generating deterministic AtlasFlow fixtures");
  run("uv", [
    "run",
    "--project",
    "services/agent-api",
    "python",
    "scripts/generate_synthetic_data.py",
  ]);

  console.log("[resolveops] applying database migrations");
  run("uv", [
    "run",
    "--project",
    "services/agent-api",
    "alembic",
    "-c",
    "services/agent-api/alembic.ini",
    "upgrade",
    "head",
  ]);

  console.log("[resolveops] seeding the bounded local demo tenant");
  run("uv", [
    "run",
    "--directory",
    "services/agent-api",
    "python",
    "-m",
    "resolveops.local_dev",
  ]);

  console.log(
    `[resolveops] starting Agent API on http://127.0.0.1:${agentApiPort}`,
  );
  agentApi = start("uv", [
    "run",
    "--directory",
    "services/agent-api",
    "uvicorn",
    "resolveops.local_dev:create_local_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    agentApiPort,
  ]);
  await waitForApi(`http://127.0.0.1:${agentApiPort}/openapi.json`, agentApi);

  console.log(
    `[resolveops] starting web app on http://127.0.0.1:${webPort}/app/cases`,
  );
  web = start("pnpm", [
    "--filter",
    "@resolveops/web",
    "exec",
    "vite",
    "--host",
    "127.0.0.1",
    "--port",
    webPort,
  ]);

  const exit = await Promise.race(
    [agentApi, web].map(
      (child) =>
        new Promise((resolve) =>
          child.once("exit", (code, signal) => resolve({ code, signal })),
        ),
    ),
  );
  stop();
  if (exit.code !== 0 && exit.signal === null) {
    process.exitCode = exit.code ?? 1;
  }
} catch (error) {
  stop();
  console.error(
    `[resolveops] local development startup failed: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exitCode = 1;
}
