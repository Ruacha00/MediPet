"use client";

import { ArrowLeft, Save } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AdminCommand, SkillType } from "./types";
import { PageIntro } from "./ui";

export function SkillForm() {
  const router = useRouter();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [skillType, setSkillType] = useState<SkillType>("instruction-only");
  const [instructions, setInstructions] = useState("");
  const [changeNote, setChangeNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const command: AdminCommand = {
      operation: "create-skill",
      payload: {
        slug,
        name,
        description,
        instructions,
        change_note: changeNote,
        skill_type: skillType,
      },
    };
    try {
      const response = await fetch("/api/admin/capabilities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(command),
      });
      const result = await response.json() as { skill_id?: string; detail?: string };
      if (!response.ok || !result.skill_id) throw new Error(result.detail ?? "Skill 创建失败");
      router.push(`/admin/capabilities/skills/${result.skill_id}`);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Skill 创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageIntro
        eyebrow="Registry / Skills / New"
        title="新建 Skill"
        description="创建不会影响当前运行时的版本 1 草稿。"
        actions={(
          <Link className="admin-text-link" href="/admin/capabilities/skills">
            <ArrowLeft size={15} /> 返回 Skills
          </Link>
        )}
      />
      <form className="admin-editor-form" onSubmit={submit}>
        <section className="admin-form-sheet" aria-labelledby="skill-metadata-title">
          <div className="admin-section-heading">
            <span>01</span>
            <div><h2 id="skill-metadata-title">发现信息</h2><p>名称与描述用于 Agent 发现。</p></div>
          </div>
          <div className="admin-field-grid">
            <label><span>Slug</span><input aria-label="Slug" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={slug} onChange={(event) => setSlug(event.target.value)} /><small>小写 kebab-case，创建后保持稳定。</small></label>
            <label><span>名称</span><input aria-label="名称" required value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label className="admin-field-wide"><span>描述</span><textarea aria-label="描述" required rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
            <label><span>Skill 类型</span><select aria-label="Skill 类型" value={skillType} onChange={(event) => setSkillType(event.target.value as SkillType)}><option value="instruction-only">Instruction only</option><option value="tool-assisted">Tool assisted</option></select></label>
            <label><span>变更说明</span><input aria-label="变更说明" required value={changeNote} onChange={(event) => setChangeNote(event.target.value)} /></label>
          </div>
        </section>
        <section className="admin-form-sheet" aria-labelledby="skill-instructions-title">
          <div className="admin-section-heading">
            <span>02</span>
            <div><h2 id="skill-instructions-title">指令正文</h2><p>源码与渲染结果并排核对。</p></div>
          </div>
          <div className="admin-markdown-editor">
            <label><span>Instructions Markdown</span><textarea aria-label="Instructions Markdown" required rows={22} value={instructions} onChange={(event) => setInstructions(event.target.value)} /></label>
            <section className="admin-markdown-preview" aria-label="Markdown 预览">
              <p className="admin-preview-label">预览</p>
              {instructions ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{instructions}</ReactMarkdown> : <p className="admin-muted">输入指令后在此预览。</p>}
            </section>
          </div>
        </section>
        {error ? <p className="admin-error" role="alert">{error}</p> : null}
        <footer className="admin-form-footer">
          <p>草稿需另行提交审核和发布。</p>
          <button className="admin-button admin-button-primary" type="submit" disabled={busy}>
            <Save size={16} /> {busy ? "正在创建" : "创建草稿"}
          </button>
        </footer>
      </form>
    </>
  );
}
