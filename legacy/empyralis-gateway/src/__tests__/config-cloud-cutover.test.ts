import test from "node:test";
import assert from "node:assert/strict";

import { assertWebSocketUrl, loadGatewayConfig } from "../config";

test("gateway development config can use localhost API fallback", () => {
  const config = loadGatewayConfig({
    HOME: "/tmp/empyralis-gateway-test",
    NODE_ENV: "development",
  });

  assert.equal(config.apiBaseUrl, "http://127.0.0.1:8001/api");
});

test("gateway production config rejects localhost API URL", () => {
  assert.throws(
    () =>
      loadGatewayConfig({
        HOME: "/tmp/empyralis-gateway-test",
        EMPYRALIS_DEPLOY_ENV: "production",
        EMPYRALIS_GATEWAY_API_URL: "http://127.0.0.1:8001/api",
      }),
    /localhost/,
  );
});

test("gateway production config requires HTTPS API URL", () => {
  assert.throws(
    () =>
      loadGatewayConfig({
        HOME: "/tmp/empyralis-gateway-test",
        EMPYRALIS_DEPLOY_ENV: "production",
        EMPYRALIS_GATEWAY_API_URL: "http://runtime.example.com/api",
      }),
    /HTTPS/,
  );
});

test("gateway production config accepts cloud HTTPS API URL", () => {
  const config = loadGatewayConfig({
    HOME: "/tmp/empyralis-gateway-test",
    EMPYRALIS_DEPLOY_ENV: "production",
    EMPYRALIS_GATEWAY_API_URL: "https://runtime.example.com/api",
    EMPYRALIS_SUPERVISOR_URL: "https://supervisor.example.com",
  });

  assert.equal(config.apiBaseUrl, "https://runtime.example.com/api");
  assert.equal(config.supervisorUrl, "https://supervisor.example.com");
});

test("gateway production websocket URL rejects localhost", () => {
  assert.throws(
    () =>
      assertWebSocketUrl("wss://localhost/gateway", {
        EMPYRALIS_DEPLOY_ENV: "production",
      } as NodeJS.ProcessEnv),
    /localhost/,
  );
});

test("gateway production websocket URL accepts cloud wss URL", () => {
  const url = assertWebSocketUrl("wss://runtime.example.com/gateway", {
    EMPYRALIS_DEPLOY_ENV: "production",
  } as NodeJS.ProcessEnv);

  assert.equal(url, "wss://runtime.example.com/gateway");
});
