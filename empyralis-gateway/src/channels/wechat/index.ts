/** Barrel export for the official-WeChat channel module. See types.ts's
 *  module doc for the architecture context (Official Account / WeCom,
 *  and why this is not a per-box "personal channel" runtime) before
 *  wiring this into anything. */
export * from "./types";
export * from "./config";
export * from "./signature";
export * from "./xml";
export * from "./token-manager";
export * from "./message-mapper";
export * from "./outbound";
export * from "./server";
