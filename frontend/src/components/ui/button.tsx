import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const button = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[var(--radius-ctl)] font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent-hover",
        secondary: "border border-border bg-surface text-text hover:bg-surface-2 hover:border-border-strong",
        ghost: "text-text-2 hover:bg-surface-3 hover:text-text",
        danger: "bg-crit text-white hover:opacity-90",
        "danger-outline": "border border-crit/40 text-crit hover:bg-crit-soft",
        link: "text-accent underline-offset-2 hover:underline px-0",
      },
      size: { sm: "h-7 px-2.5 text-[12.5px]", md: "h-8 px-3 text-[13px]", lg: "h-10 px-4 text-sm", icon: "size-8" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp ref={ref} className={cn(button({ variant, size }), className)} {...props} />;
});
Button.displayName = "Button";
