"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  Bot,
  Brain,
  Command,
  CreditCard,
  Cpu,
  Home,
  MessageSquare,
  Moon,
  Plug,
  Radio,
  Sun,
  type LucideIcon,
} from "lucide-react";

import type { FleetTheme } from "./fleet-preferences";
import { useFleetAgents } from "./fleet-data";
import { isSageAgent, toAgentSummary } from "./fleet-presentation";

type Action = {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  run: () => void;
};

/**
 * Cmd/Ctrl+K command palette. Fast filter, keyboard-navigable, no library
 * dependency. Actions: navigate to any section, open an agent by name,
 * toggle theme, "Chat with Sage".
 */
export function FleetCommandPalette({
  workspaceId,
  theme,
  onToggleTheme,
}: {
  workspaceId: string;
  theme: FleetTheme;
  onToggleTheme: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const { agents } = useFleetAgents(workspaceId);

  // Global Cmd+K binding
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const modifier = e.metaKey || e.ctrlKey;
      if (modifier && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const close = useCallback(() => setOpen(false), []);
  const go = useCallback(
    (path: string) => {
      router.push(path);
      close();
    },
    [router, close],
  );

  const actions: Action[] = useMemo(() => {
    const base = `/w/${encodeURIComponent(workspaceId)}`;
    const navigation: Action[] = [
      { id: "nav-home", label: "Home", group: "Navigate", icon: Home, run: () => go(`${base}/fleet`) },
      { id: "nav-agents", label: "Agents", group: "Navigate", icon: Bot, run: () => go(`${base}/fleet`) },
      { id: "nav-channels", label: "Channels", group: "Navigate", icon: Radio, run: () => go(`${base}/channels`) },
      { id: "nav-connectors", label: "Connectors", group: "Navigate", icon: Plug, run: () => go(`${base}/integrations`) },
      { id: "nav-hardware", label: "Hardware", group: "Navigate", icon: Cpu, run: () => go(`${base}/hardware`) },
      { id: "nav-memory", label: "Memory", group: "Navigate", icon: Brain, run: () => go(`${base}/memory`) },
      { id: "nav-billing", label: "Billing", group: "Navigate", icon: CreditCard, run: () => go(`${base}/settings`) },
    ];

    const summaries = agents.map((a, i) => toAgentSummary(a, i));
    const sage = summaries.find(isSageAgent);
    const commands: Action[] = [
      {
        id: "chat-sage",
        label: sage ? "Chat with Sage" : "Open chat",
        hint: "→ chat",
        group: "Commands",
        icon: MessageSquare,
        run: () => go(`${base}/chat`),
      },
      {
        id: "toggle-theme",
        label: theme === "dark" ? "Switch to light" : "Switch to dark",
        hint: "theme",
        group: "Commands",
        icon: theme === "dark" ? Sun : Moon,
        run: () => {
          onToggleTheme();
          close();
        },
      },
    ];

    const agentActions: Action[] = summaries
      .filter((a) => !isSageAgent(a))
      .map((a) => ({
        id: `agent-${a.id}`,
        label: a.name,
        hint: "open",
        group: "Agents",
        icon: Bot,
        run: () => go(`${base}/fleet`),
      }));

    return [...commands, ...navigation, ...agentActions];
  }, [workspaceId, theme, agents, go, onToggleTheme, close]);

  const filtered = useMemo(() => {
    if (!query.trim()) return actions;
    const q = query.toLowerCase();
    return actions.filter((a) => a.label.toLowerCase().includes(q));
  }, [actions, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const onListKey = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(filtered.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        filtered[activeIndex]?.run();
      }
    },
    [filtered, activeIndex],
  );

  // Ensure active item stays in view
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    if (!list) return;
    const item = list.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    item?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  if (!open) return null;

  // Group by group, preserving order.
  const grouped: { group: string; items: { action: Action; index: number }[] }[] = [];
  filtered.forEach((action, index) => {
    const bucket = grouped.find((g) => g.group === action.group);
    if (bucket) bucket.items.push({ action, index });
    else grouped.push({ group: action.group, items: [{ action, index }] });
  });

  return (
    <div
      className="fleet-palette-backdrop"
      onClick={close}
      onKeyDown={onListKey}
      role="presentation"
    >
      <div className="fleet-palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
        <div className="fleet-palette-input-wrap">
          <span className="fleet-palette-item-icon">
            <Command size={16} strokeWidth={1.75} />
          </span>
          <input
            ref={inputRef}
            className="fleet-palette-input"
            placeholder="Type a command or search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onListKey}
          />
          <span className="fleet-palette-hint">Esc</span>
        </div>
        <div className="fleet-palette-list" ref={listRef}>
          {filtered.length === 0 && (
            <div className="fleet-palette-empty">No matches</div>
          )}
          {grouped.map((g) => (
            <div key={g.group}>
              <div className="fleet-palette-group-label">{g.group}</div>
              {g.items.map(({ action, index }) => {
                const Icon = action.icon;
                const active = index === activeIndex;
                return (
                  <button
                    key={action.id}
                    type="button"
                    className={`fleet-palette-item${active ? " fleet-palette-item--active" : ""}`}
                    data-index={index}
                    onMouseMove={() => setActiveIndex(index)}
                    onClick={action.run}
                  >
                    <span className="fleet-palette-item-icon">
                      <Icon size={16} strokeWidth={1.75} />
                    </span>
                    <span className="fleet-palette-item-label">{action.label}</span>
                    {action.hint && <span className="fleet-palette-item-hint">{action.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
