import { backendBaseUrl } from "./chat-config";

export const serverBackendBaseUrl =
  process.env.MEDIPET_INTERNAL_API_URL ?? backendBaseUrl;
