export interface ObjectStorage {
  get(key: string): Promise<string | null>;
  list(prefix: string, limit: number): Promise<readonly string[]>;
}

export class MemoryObjectStorage implements ObjectStorage {
  readonly #objects: ReadonlyMap<string, string>;

  constructor(objects: Readonly<Record<string, string>>) {
    this.#objects = new Map(Object.entries(objects));
  }

  async get(key: string): Promise<string | null> {
    return this.#objects.get(key) ?? null;
  }

  async list(prefix: string, limit: number): Promise<readonly string[]> {
    return [...this.#objects.keys()]
      .filter((key) => key.startsWith(prefix))
      .sort()
      .slice(0, limit);
  }
}

interface R2ObjectBody {
  text(): Promise<string>;
}

interface R2ListedObject {
  key: string;
}

interface R2Objects {
  objects: readonly R2ListedObject[];
  truncated: boolean;
  cursor?: string;
}

export interface R2BucketLike {
  get(key: string): Promise<R2ObjectBody | null>;
  list(options: {
    prefix: string;
    limit: number;
    cursor?: string;
  }): Promise<R2Objects>;
}

/** Narrow adapter around the only R2 operations synthetic routes are allowed to use. */
export class R2ObjectStorage implements ObjectStorage {
  readonly #bucket: R2BucketLike;

  constructor(bucket: R2BucketLike) {
    this.#bucket = bucket;
  }

  async get(key: string): Promise<string | null> {
    const object = await this.#bucket.get(key);
    return object === null ? null : object.text();
  }

  async list(prefix: string, limit: number): Promise<readonly string[]> {
    const keys: string[] = [];
    let cursor: string | undefined;
    do {
      const remaining = limit - keys.length;
      if (remaining <= 0) {
        break;
      }
      const options: { prefix: string; limit: number; cursor?: string } = {
        prefix,
        limit: Math.min(remaining, 1_000),
      };
      if (cursor !== undefined) {
        options.cursor = cursor;
      }
      const page = await this.#bucket.list(options);
      keys.push(...page.objects.map((object) => object.key));
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor !== undefined);
    return keys;
  }
}
