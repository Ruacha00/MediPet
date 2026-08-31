import { afterEach, describe, expect, it, vi } from "vitest";

describe("chat API endpoint configuration", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("uses the Compose service address only for server-side requests", async () => {
    vi.stubEnv("MEDIPET_INTERNAL_API_URL", "http://api:8000");
    vi.stubEnv("NEXT_PUBLIC_MEDIPET_API_URL", "http://localhost:8000");

    const { backendBaseUrl } = await import("./chat-config");
    const { serverBackendBaseUrl } = await import("./chat-config.server");

    expect(serverBackendBaseUrl).toBe("http://api:8000");
    expect(backendBaseUrl).toBe("http://localhost:8000");
  });
});
