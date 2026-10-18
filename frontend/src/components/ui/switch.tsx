import * as S from "@radix-ui/react-switch";

export function Switch({ checked, onCheckedChange, id, disabled, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; id?: string; disabled?: boolean; label?: string }) {
  return (
    <S.Root id={id} checked={checked} disabled={disabled} onCheckedChange={onCheckedChange} aria-label={label}
      className="relative h-[18px] w-8 shrink-0 rounded-full bg-border-strong transition-colors data-[state=checked]:bg-accent disabled:opacity-50">
      <S.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[16px]" />
    </S.Root>
  );
}
