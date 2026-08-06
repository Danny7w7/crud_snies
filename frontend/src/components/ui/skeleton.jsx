import { cn } from "./utils";

function Skeleton({ className, ...props }) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("erp-skeleton rounded-lg bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
