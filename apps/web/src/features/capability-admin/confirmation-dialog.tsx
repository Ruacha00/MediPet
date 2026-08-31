"use client";

import { useEffect, useRef } from "react";

export type AdminConfirmation = {
  title: string;
  message: string;
  confirmLabel: string;
};

export function ConfirmationDialog({
  confirmation,
  busy,
  onCancel,
  onConfirm,
}: {
  confirmation: AdminConfirmation;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    cancelRef.current?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const controls = Array.from(dialogRef.current.querySelectorAll<HTMLButtonElement>(
        "button:not(:disabled)",
      ));
      if (!controls.length) return;
      const first = controls[0];
      const last = controls.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [busy, onCancel]);

  return (
    <div className="admin-dialog-backdrop">
      <section ref={dialogRef} className="admin-confirm-dialog" role="alertdialog" aria-modal="true" aria-label={confirmation.title}>
        <p className="admin-eyebrow">Runtime impact</p>
        <h2>{confirmation.title}</h2>
        <p>{confirmation.message}</p>
        <div><button ref={cancelRef} className="admin-button admin-button-quiet" type="button" disabled={busy} onClick={onCancel}>取消</button><button className="admin-button admin-button-danger" type="button" disabled={busy} onClick={onConfirm}>{confirmation.confirmLabel}</button></div>
      </section>
    </div>
  );
}
