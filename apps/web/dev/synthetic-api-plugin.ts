import type { IncomingHttpHeaders } from "node:http";

import type { Plugin } from "vite";

import {
  createSyntheticApi,
  InMemoryNonceStore,
} from "../worker/synthetic-api/index.ts";
import { FileObjectStorage } from "./filesystem-storage.ts";

interface LocalSyntheticApiOptions {
  fixtureRoot: string;
  hmacSecret: string;
}

function requestHeaders(headers: IncomingHttpHeaders): Headers {
  const result = new Headers();
  for (const [name, value] of Object.entries(headers)) {
    if (typeof value === "string") result.set(name, value);
    if (Array.isArray(value)) {
      for (const item of value) result.append(name, item);
    }
  }
  return result;
}

/** Mount the production synthetic route handlers inside Vite for local development. */
export function localSyntheticApiPlugin(
  options: LocalSyntheticApiOptions,
): Plugin {
  const api = createSyntheticApi({
    storage: new FileObjectStorage(options.fixtureRoot),
    hmacSecret: options.hmacSecret,
    nonceStore: new InMemoryNonceStore(),
  });

  return {
    name: "resolveops-local-synthetic-api",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const url = new URL(request.url ?? "/", "http://resolveops.local");
        const isSyntheticRoute =
          url.pathname === "/api/v1/cases" ||
          url.pathname.startsWith("/api/v1/cases/") ||
          url.pathname === "/api/v1/public/replays" ||
          url.pathname.startsWith("/api/v1/public/replays/") ||
          url.pathname === "/systems/v1" ||
          url.pathname.startsWith("/systems/v1/");
        if (!isSyntheticRoute) {
          next();
          return;
        }

        try {
          const result = await api.fetch(
            new Request(url, {
              method: request.method ?? "GET",
              headers: requestHeaders(request.headers),
            }),
          );
          response.statusCode = result.status;
          response.statusMessage = result.statusText;
          result.headers.forEach((value, name) => {
            response.setHeader(name, value);
          });
          response.end(Buffer.from(await result.arrayBuffer()));
        } catch (error) {
          next(error);
        }
      });
    },
  };
}
