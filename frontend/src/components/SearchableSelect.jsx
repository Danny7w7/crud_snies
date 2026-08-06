import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Skeleton } from "./ui/skeleton";
import { cn } from "./ui/utils";

export function SearchableSelect({
  id,
  value,
  options,
  onValueChange,
  placeholder = "Seleccione una opción",
  searchPlaceholder = "Buscar...",
  emptyLabel = "Sin resultados.",
  disabled = false,
  isLoading = false,
  className,
  maxMenuHeightClassName = "max-h-[220px]",
  onSearchChange,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef(null);
  const generatedListboxId = useId();
  const listboxId = `${id || generatedListboxId}-options`;

  const selectedLabel = useMemo(() => {
    const selected = options.find((option) => option.value === value);
    return selected?.label ?? placeholder;
  }, [options, placeholder, value]);

  const filteredOptions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) {
      return options;
    }

    return options.filter((option) =>
      `${option.label} ${option.value}`.toLowerCase().includes(normalizedQuery),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }

    requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  }, [open]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-controls={listboxId}
          disabled={disabled}
          className={cn(
            "flex h-10 w-full min-w-0 items-center justify-between rounded-md border border-border bg-secondary px-3 py-2 text-sm font-normal text-foreground",
            "h-11 rounded-xl bg-input-background transition",
            "hover:border-accent/70 hover:bg-input-background hover:text-foreground",
            "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20",
            "disabled:cursor-not-allowed disabled:opacity-50",
            className,
          )}
        >
          <span className="truncate text-left">{selectedLabel}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>

      <PopoverContent className="z-[1000] w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <div className="border-b border-border px-3 py-2">
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              onSearchChange?.(event.target.value);
            }}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div id={listboxId} role="listbox" className={cn(maxMenuHeightClassName, "overflow-y-auto p-1")}>
          {isLoading ? (
            <div className="space-y-2 px-2 py-3" role="status" aria-label="Cargando opciones">
              {Array.from({ length: 4 }, (_, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Skeleton className="h-4 w-4 shrink-0 rounded" />
                  <Skeleton className="h-4 flex-1" />
                </div>
              ))}
              <span className="sr-only">Cargando opciones</span>
            </div>
          ) : filteredOptions.length === 0 ? (
            <div className="px-2 py-6 text-center text-sm text-muted-foreground">{emptyLabel}</div>
          ) : (
            filteredOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={value === option.value}
                disabled={option.disabled}
                title={option.disabledTooltip}
                onClick={() => {
                  if (option.disabled) return;
                  onValueChange(option.value);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-foreground transition-colors",
                  option.disabled
                    ? "cursor-not-allowed text-muted-foreground hover:bg-transparent"
                    : "hover:bg-secondary",
                )}
              >
                <Check
                  className={cn(
                    "h-4 w-4",
                    value === option.value ? "opacity-100 text-foreground" : "opacity-0",
                    option.disabled && "text-muted-foreground",
                  )}
                />
                <span className="truncate">{option.label}</span>
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
