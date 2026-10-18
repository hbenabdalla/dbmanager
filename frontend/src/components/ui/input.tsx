import { cloneElement, forwardRef, isValidElement, useId, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const base =
  "w-full rounded-[var(--radius-ctl)] border border-border bg-surface px-2.5 text-[13px] text-text placeholder:text-muted/80 transition-colors hover:border-border-strong focus:border-accent focus:outline-none focus:ring-2 focus:ring-[var(--ring)] disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(base, "h-8", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(base, "py-2 font-mono text-[12.5px]", className)} {...p} />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...p }, ref) => (
  <select ref={ref} className={cn(base, "h-8 appearance-none bg-[length:16px] bg-[right_6px_center] bg-no-repeat pr-7", className)}
    style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2364748a' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }} {...p}>
    {children}
  </select>
));
Select.displayName = "Select";

export function Label({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("mb-1 block text-[12.5px] font-medium text-text-2", className)} {...p} />;
}

export function Field({ label, hint, error, children, className }: { label: string; hint?: string; error?: string; children: React.ReactNode; className?: string }) {
  const auto = useId();
  // Associate the label (and hint/error) with the control for assistive technology.
  const child = isValidElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean }>(children) ? children : null;
  const id = child?.props.id ?? auto;
  const describedBy = error || hint ? `${id}-desc` : undefined;
  return (
    <div className={className}>
      <Label htmlFor={id}>{label}</Label>
      {child ? cloneElement(child, { id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined }) : children}
      {error ? <p id={describedBy} className="mt-1 text-[12px] text-crit">{error}</p> : hint ? <p id={describedBy} className="mt-1 text-[12px] text-muted">{hint}</p> : null}
    </div>
  );
}
