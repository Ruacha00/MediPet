import type { Metadata } from "next";

import "./globals.css";
import "../features/capability-admin/capability-admin.css";

export const metadata: Metadata = {
  title: "MediPet · 门诊就诊助手",
  description: "面向单医院门诊场景的智能就诊助手",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
