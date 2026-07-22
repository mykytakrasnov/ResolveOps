import {
  createSyntheticApi,
  type NonceStore,
  R2ObjectStorage,
} from "./synthetic-api/index.ts";
import type { R2BucketLike } from "./synthetic-api/storage.ts";

export * from "./synthetic-api/index.ts";

export interface WorkerEnv {
  SYNTHETIC_DATA_R2: R2BucketLike;
  SYNTHETIC_API_HMAC_SECRET: string;
  /** Must be backed by an atomic, durable store in every deployed environment. */
  SYNTHETIC_NONCE_STORE: NonceStore;
}

export default {
  fetch(request: Request, env: WorkerEnv): Promise<Response> {
    return createSyntheticApi({
      storage: new R2ObjectStorage(env.SYNTHETIC_DATA_R2),
      hmacSecret: env.SYNTHETIC_API_HMAC_SECRET,
      nonceStore: env.SYNTHETIC_NONCE_STORE,
    }).fetch(request);
  },
};
