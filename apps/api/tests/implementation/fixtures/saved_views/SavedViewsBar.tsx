/**
 * Saving and reopening a conversion view.
 *
 * The control names the current filter stack and saves it; the selector reopens
 * a saved stack. Both carry stable test ids so the uTrace browser SDK's
 * annotations identify them by identity rather than by visible label.
 */

import { useState } from "react";
import { BookmarkPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SavedConversionView } from "@/lib/dashboard/saved-views";

interface Props {
  views: readonly SavedConversionView[];
  selectedViewId: string | null;
  onSave: (name: string) => void;
  onOpen: (id: string) => void;
}

export function SavedViewsBar({ views, selectedViewId, onSave, onOpen }: Props) {
  const [name, setName] = useState("");
  const trimmed = name.trim();

  return (
    <div
      className="flex flex-wrap items-end gap-3 rounded-xl border bg-card p-4"
      data-testid="saved-views-bar"
    >
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          Save this view
        </span>
        <div className="flex items-center gap-2">
          <Input
            className="h-9 w-64"
            placeholder="Name this view"
            value={name}
            data-testid="saved-view-name"
            onChange={(event) => setName(event.target.value)}
          />
          <Button
            type="button"
            size="sm"
            className="h-9 gap-1.5 font-normal"
            disabled={trimmed.length === 0}
            data-testid="saved-view-save"
            onClick={() => {
              onSave(trimmed);
              setName("");
            }}
          >
            <BookmarkPlus className="size-3.5" />
            Save view
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          Saved views
        </span>
        <select
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={selectedViewId ?? ""}
          data-testid="saved-view-select"
          onChange={(event) => {
            if (event.target.value !== "") {
              onOpen(event.target.value);
            }
          }}
        >
          <option value="">
            {views.length === 0 ? "No saved views yet" : "Reopen a saved view"}
          </option>
          {views.map((view) => (
            <option key={view.id} value={view.id} data-testid={`saved-view-option-${view.id}`}>
              {view.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
