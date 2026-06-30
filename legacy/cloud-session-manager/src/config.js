const env = (key, fallback) => {
  const value = process.env[key];
  return value !== undefined && value !== "" ? value : fallback;
};
const envInt = (key, fallback) => {
  const value = parseInt(process.env[key], 10);
  return Number.isFinite(value) ? value : fallback;
};

export const CONFIG = {
  // Redis for outbox + session storage (Stage 5)
  sessionRedisUrl: env("SESSION_REDIS_URL", "redis://localhost:6379"),

  // KMS (Stage 5 — placeholder for now)
  kmsKeyArn: env("KMS_KEY_ARN", ""),
  kmsRegion: env("KMS_REGION", "us-east-1"),

  // API server
  apiPort: envInt("API_PORT", 3400),
  apiSecret: env("API_SECRET", ""),

  // GramJS credentials
  gramjsApiId: envInt("GRAMJS_API_ID", 0),
  gramjsApiHash: env("GRAMJS_API_HASH", ""),

  // Session pool
  maxSessions: envInt("MAX_SESSIONS", 50),
  sessionTtlMs: envInt("SESSION_TTL_MS", 30 * 60 * 1000),

  // Rate limiting defaults (Stage 4)
  rateLimitMaxBurst: envInt("RATE_LIMIT_MAX_BURST", 20),
  rateLimitRefillPerMinute: envInt("RATE_LIMIT_REFILL_PER_MINUTE", 20),

  // GramJS connection
  gramjsConnectionRetries: envInt("GRAMJS_CONNECTION_RETRIES", 5),
  gramjsReconnectBaseDelayMs: envInt("GRAMJS_RECONNECT_BASE_DELAY_MS", 1000),
  gramjsReconnectMaxDelayMs: envInt("GRAMJS_RECONNECT_MAX_DELAY_MS", 30000),

  // Stage 2: backend integration
  backendUrl: env("BACKEND_URL", "http://localhost:8001"),
  backendHmacSecret: env("BACKEND_HMAC_SECRET", env("API_SECRET", "dev-secret-change-me")),
};

  // Stage 6: enable cloud session manager routing
