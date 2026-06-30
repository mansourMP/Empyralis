import Redis from "ioredis";
import { CONFIG } from "./config.js";

/**
 * PerSessionRateLimiter — Redis-backed token bucket.
 *
 * Capacity: 20 tokens (matching Telegram's ~20 msg/min for user accounts)
 * Refill: 1 token every 3 seconds (steady-state 20/min)
 *
 * Each session gets its own Redis key: session:{sessionId}:tokens
 * Stored as a JSON object: { tokens: number, lastRefill: ISO string }
 *
 * consume() is atomic via Redis MULTI/EXEC, safe under concurrent access.
 */
export class PerSessionRateLimiter {
  /** @param {string} sessionId */
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.key = `session:${sessionId}:tokens`;
    this.maxTokens = CONFIG.rateLimitMaxBurst || 20;
    this.refillIntervalMs = Math.round(60_000 / (CONFIG.rateLimitRefillPerMinute || 20)); // 3000ms default
    this.redis = new Redis(CONFIG.sessionRedisUrl, {
      lazyConnect: true,
      maxRetriesPerRequest: 2,
      retryStrategy: (times) => Math.min(times * 200, 2000),
    });
  }

  async connect() {
    if (this.redis.status !== "ready" && this.redis.status !== "connecting") {
      await this.redis.connect();
    }
  }

  async disconnect() {
    if (this.redis.status !== "end") {
      await this.redis.quit().catch(() => {});
    }
  }

  /**
   * Attempt to consume `cost` tokens (default 1).
   * Returns { allowed: true, tokensLeft: number } or
   *         { allowed: false, retryAfterMs: number, tokensLeft: number }
   */
  async consume(cost = 1) {
    await this.connect();

    const now = Date.now();
    const script = `
      local key = KEYS[1]
      local cost = tonumber(ARGV[1])
      local maxTokens = tonumber(ARGV[2])
      local refillMs = tonumber(ARGV[3])
      local now = tonumber(ARGV[4])

      local raw = redis.call("GET", key)
      local tokens = maxTokens
      local lastRefill = now

      if raw then
        local data = cjson.decode(raw)
        tokens = tonumber(data.tokens) or maxTokens
        lastRefill = tonumber(data.lastRefill) or now
      end

      -- Refill tokens based on elapsed time
      local elapsed = now - lastRefill
      if elapsed > 0 then
        local toAdd = math.floor(elapsed / refillMs)
        if toAdd > 0 then
          tokens = math.min(maxTokens, tokens + toAdd)
          lastRefill = lastRefill + (toAdd * refillMs)
        end
      end

      -- Check if we have enough
      if tokens >= cost then
        tokens = tokens - cost
        redis.call("SET", key, cjson.encode({ tokens = tokens, lastRefill = lastRefill }))
        redis.call("EXPIRE", key, 86400)  -- 24h TTL
        return cjson.encode({ allowed = true, tokensLeft = tokens })
      else
        -- Not enough tokens — calculate wait time
        local deficit = cost - tokens
        local neededRefills = math.ceil(deficit)
        local retryAfterMs = neededRefills * refillMs
        redis.call("SET", key, cjson.encode({ tokens = tokens, lastRefill = lastRefill }))
        redis.call("EXPIRE", key, 86400)
        return cjson.encode({ allowed = false, retryAfterMs = retryAfterMs, tokensLeft = tokens })
      end
    `;

    const result = await this.redis.eval(script, 1, this.key, String(cost), String(this.maxTokens), String(this.refillIntervalMs), String(now));
    return JSON.parse(result);
  }

  /**
   * Get current token count without consuming.
   */
  async getStatus() {
    await this.connect();
    const raw = await this.redis.get(this.key);
    if (!raw) return { tokensLeft: this.maxTokens, maxTokens: this.maxTokens };
    try {
      const data = JSON.parse(raw);
      const tokens = Number(data.tokens) || 0;
      const lastRefill = Number(data.lastRefill) || Date.now();
      const elapsed = Date.now() - lastRefill;
      const toAdd = Math.floor(elapsed / this.refillIntervalMs);
      const tokensLeft = Math.min(this.maxTokens, tokens + toAdd);
      return { tokensLeft, maxTokens: this.maxTokens };
    } catch {
      return { tokensLeft: this.maxTokens, maxTokens: this.maxTokens };
    }
  }

  /** Reset tokens to full (for testing) */
  async reset() {
    await this.connect();
    const data = JSON.stringify({ tokens: this.maxTokens, lastRefill: Date.now() });
    await this.redis.set(this.key, data);
  }

  /** Clear the key (for testing) */
  async clear() {
    await this.connect();
    await this.redis.del(this.key);
  }
}
