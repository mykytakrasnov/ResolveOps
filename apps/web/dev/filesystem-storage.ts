import { readdir, readFile } from "node:fs/promises";
import type { Dirent } from "node:fs";
import path from "node:path";

import type { ObjectStorage } from "../worker/synthetic-api/storage.ts";

function normalizedKey(key: string): string {
  if (
    key.length === 0 ||
    key.includes("\0") ||
    key.includes("\\") ||
    path.posix.isAbsolute(key) ||
    key.split("/").some((part) => part === "..")
  ) {
    throw new Error(
      "Synthetic object key must remain within the fixture root.",
    );
  }
  return key.replace(/^\.\//, "");
}

function isMissingFile(error: unknown): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    (error.code === "ENOENT" || error.code === "EISDIR")
  );
}

/** Development-only adapter over generated AtlasFlow fixture files. */
export class FileObjectStorage implements ObjectStorage {
  readonly #root: string;

  constructor(root: string) {
    this.#root = path.resolve(root);
  }

  async get(key: string): Promise<string | null> {
    const filename = path.resolve(this.#root, normalizedKey(key));
    if (
      filename !== this.#root &&
      !filename.startsWith(`${this.#root}${path.sep}`)
    ) {
      throw new Error("Synthetic object key escaped the fixture root.");
    }
    try {
      return await readFile(filename, "utf8");
    } catch (error) {
      if (isMissingFile(error)) return null;
      throw error;
    }
  }

  async list(prefix: string, limit: number): Promise<readonly string[]> {
    const safePrefix = normalizedKey(prefix);
    const directory = path.resolve(this.#root, safePrefix);
    if (!directory.startsWith(`${this.#root}${path.sep}`)) {
      throw new Error("Synthetic object prefix escaped the fixture root.");
    }

    const keys: string[] = [];
    const visit = async (current: string): Promise<void> => {
      let entries: Dirent[];
      try {
        entries = await readdir(current, { withFileTypes: true });
      } catch (error) {
        if (isMissingFile(error)) return;
        throw error;
      }
      for (const entry of entries.sort((left, right) =>
        left.name.localeCompare(right.name),
      )) {
        if (keys.length >= limit) return;
        const filename = path.join(current, entry.name);
        if (entry.isDirectory()) {
          await visit(filename);
        } else if (entry.isFile()) {
          keys.push(
            path.relative(this.#root, filename).split(path.sep).join("/"),
          );
        }
      }
    };

    await visit(directory);
    return keys.sort().slice(0, limit);
  }
}
