"use client";

import { ArrowLeft, Clock3, PawPrint, Puzzle, ScrollText } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const links = [
  { href: "/admin/capabilities/skills", label: "Skills", icon: ScrollText },
  { href: "/admin/capabilities/tools", label: "Tools", icon: Puzzle },
  { href: "/admin/capabilities/audits", label: "变更记录", icon: Clock3 },
];

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="capability-admin-shell">
      <aside className="admin-rail">
        <header className="admin-brand"><span><PawPrint size={21} /></span><div><strong>MediPet</strong><small>能力治理台</small></div></header>
        <div className="admin-environment"><i aria-hidden="true" />非生产环境</div>
        <nav aria-label="能力治理导航">
          {links.map(({ href, label, icon: Icon }) => (
            <Link className={pathname.startsWith(href) ? "active" : ""} href={href} key={href}><Icon size={18} /><span>{label}</span></Link>
          ))}
        </nav>
        <footer><Link href="/"><ArrowLeft size={16} /> 返回对话</Link><p>Development admin<br />门禁接缝已预留</p></footer>
      </aside>
      <div className="admin-workspace">
        <div className="admin-workspace-banner"><span>开发能力治理</span><small>Registry facts · Server authorized</small></div>
        <div className="admin-scroll-region"><div className="admin-page-frame">{children}</div></div>
      </div>
    </div>
  );
}
