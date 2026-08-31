import { CircleAlert } from "lucide-react";

import { CapabilityAdminError } from "./server";

export function AdminLoadError({ error }: { error: unknown }) {
  const message = error instanceof CapabilityAdminError
    ? error.message
    : "能力治理服务暂时不可用";
  return (
    <section className="admin-load-error" role="alert">
      <CircleAlert aria-hidden="true" size={25} />
      <div><p className="admin-eyebrow">Registry unavailable</p><h1>暂时无法加载治理台</h1><p>{message}</p><small>请检查 API 服务及服务端 MEDIPET_MANAGEMENT_TOKEN 配置。页面未加载部分数据，也不会提供变更操作。</small></div>
    </section>
  );
}
