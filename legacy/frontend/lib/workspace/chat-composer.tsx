'use client';

import type { DragEvent, FormEvent, KeyboardEvent, ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  BadgePlus,
  Blocks,
  BookOpen,
  Brain,
  CalendarDays,
  ChevronRight,
  Command as CommandIcon,
  Cpu,
  FileSearch,
  Globe2,
  Image as ImageIcon,
  Laptop,
  ListTodo,
  Mail,
  Mic,
  MicOff,
  Paperclip,
  Plug,
  PlugZap,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  SquareStack,
  SquareTerminal,
  Table,
  WandSparkles,
  X,
  type LucideIcon,
} from 'lucide-react';

import { PlatformNotification } from '@/lib/ui/platform-notification';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';

type ComposerOption = {
  value: string;
  label: string;
  detail?: string;
  disabled?: boolean;
};

export type ComposerToolItem = {
  id: string;
  label: string;
  detail?: string;
  enabled: boolean;
};

export type ComposerToolGroup = {
  id: string;
  label: string;
  items: ComposerToolItem[];
};

export type ComposerPreRunCostEstimate = {
  estimateLabel: string;
  detail: string;
  warnings: string[];
};

export type ComposerSlashCommand = {
  id: string;
  slash: `/${string}`;
  title: string;
  description: string;
  category?: string;
  keywords?: readonly string[];
  icon: LucideIcon;
};

export type ComposerActionMenuItem = {
  id: string;
  title: string;
  description: string;
  icon?: LucideIcon;
  onSelect: () => void;
};

export type ComposerCapabilityStatusTone = 'ready' | 'setup' | 'warning' | 'neutral';

export type ComposerCapabilitySubItem = {
  id: string;
  title: string;
  detail?: string;
  status?: string;
  statusTone?: ComposerCapabilityStatusTone;
  icon?: LucideIcon;
  iconSrc?: string;
  enabled?: boolean;
  itemType?: 'action' | 'toggle';
  dividerBefore?: boolean;
  onSelect?: () => void;
};

export type ComposerCapabilityItem = {
  id: string;
  title: string;
  status?: string;
  statusTone?: ComposerCapabilityStatusTone;
  icon?: LucideIcon;
  iconSrc?: string;
  submenuTitle?: string;
  submenuItems?: readonly ComposerCapabilitySubItem[];
  onSelect: () => void;
};

export type ComposerComputerStatus = {
  online: boolean;
  label: string;
  selectedValue?: string;
  options?: ComposerOption[];
  onChange?: (nextValue: string) => void;
  onOpenSettings?: () => void;
};

function composerOptionLabel(options: ComposerOption[], value: string): string {
  for (const option of options) {
    if (option.value === value) {
      return option.label;
    }
  }
  return '';
}

function capabilityOptionIcon(id: string): LucideIcon {
  const normalizedId = id.toLowerCase();
  if (normalizedId.startsWith('connector:')) {
    return Plug;
  }
  if (normalizedId.startsWith('skill:')) {
    if (normalizedId.includes('calendar')) {
      return CalendarDays;
    }
    if (normalizedId.includes('code') || normalizedId.includes('shell') || normalizedId.includes('terminal')) {
      return SquareTerminal;
    }
    if (normalizedId.includes('file')) {
      return FileSearch;
    }
    if (normalizedId.includes('gmail') || normalizedId.includes('mail')) {
      return Mail;
    }
    if (normalizedId.includes('http') || normalizedId.includes('web') || normalizedId.includes('search')) {
      return Globe2;
    }
    return SquareStack;
  }
  switch (id) {
    case 'files':
      return Paperclip;
    case 'browser':
      return Globe2;
    case 'computer':
      return Laptop;
    case 'connectors':
      return Blocks;
    case 'skills':
      return WandSparkles;
    case 'manage_skills':
    case 'manage_connectors':
      return SlidersHorizontal;
    case 'add_skills':
      return BadgePlus;
    case 'add_connector':
      return PlugZap;
    case 'tasks':
      return ListTodo;
    case 'memory':
      return Brain;
    case 'research':
      return Search;
    case 'image_create':
      return ImageIcon;
    case 'image_edit':
      return Sparkles;
    case 'slides':
      return BookOpen;
    case 'website':
      return Globe2;
    case 'spreadsheet':
      return Table;
    case 'approvals':
      return ShieldCheck;
    case 'model':
      return Cpu;
    case 'commands':
      return CommandIcon;
    default:
      return Sparkles;
  }
}

function CapabilityIconMark({
  icon: Icon,
  iconSrc,
}: {
  icon: LucideIcon;
  iconSrc?: string;
}) {
  if (iconSrc) {
    return (
      <img
        src={iconSrc}
        alt=""
        aria-hidden="true"
        className="app-chat-composer__capability-logo"
      />
    );
  }
  return <Icon size={16} strokeWidth={1.9} aria-hidden="true" />;
}

export function ChatComposer({
  draft,
  onDraftChange,
  onSubmit,
  onStop,
  capabilityItems = [],
  onFilesSelected,
  modelControl,
  modelControlOpen = false,
  onComposerMenuOpen,
  reasoningEffort,
  reasoningOptions,
  onReasoningEffortChange,
  controlsDisabled = false,
  sendDisabled = false,
  placeholder = '',
  busy = false,
  smallModelWarning = null,
  onDismissSmallModelWarning,
  slashCommands = [],
  onSlashCommandSelect,
  onVoiceTranscribe,
  attachments = [],
  onRemoveAttachment,
}: {
  draft: string;
  onDraftChange: (nextDraft: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  capabilityItems?: readonly ComposerCapabilityItem[];
  onFilesSelected?: (files: File[]) => void | Promise<void>;
  modelControl?: ReactNode;
  modelControlOpen?: boolean;
  onComposerMenuOpen?: () => void;
  reasoningEffort: string;
  reasoningOptions: ComposerOption[];
  onReasoningEffortChange: (nextValue: string) => void;
  contextWindowLabel?: string | null;
  controlsDisabled?: boolean;
  sendDisabled?: boolean;
  placeholder?: string;
  providerGateVisible?: boolean;
  providerSummary?: {
    label: string;
    actionLabel?: string;
  } | null;
  busy?: boolean;
  smallModelWarning?: string | null;
  onDismissSmallModelWarning?: () => void;
  preRunCostEstimate?: ComposerPreRunCostEstimate | null;
  slashCommands?: readonly ComposerSlashCommand[];
  onSlashCommandSelect?: (command: ComposerSlashCommand) => void;
  actionMenuItems?: readonly ComposerActionMenuItem[];
  onVoiceTranscribe?: (audio: Blob) => Promise<string>;
  attachments?: any[];
  onRemoveAttachment?: (attachment: any) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const actionLauncherRef = useRef<HTMLDivElement | null>(null);
  const commandPaletteRef = useRef<HTMLDivElement | null>(null);
  const fileDragDepthRef = useRef(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const draftRef = useRef(draft);
  const [actionPaletteOpen, setActionPaletteOpen] = useState(false);
  const [voiceState, setVoiceState] = useState<'idle' | 'recording' | 'transcribing'>('idle');
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [fileDragActive, setFileDragActive] = useState(false);
  const [commandPaletteDismissed, setCommandPaletteDismissed] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const hasDraft = draft.trim().length > 0 || attachments.length > 0;
  const canSend = !busy && !sendDisabled && (hasDraft);
  const canStop = busy && typeof onStop === 'function';
  const showSendButton = busy || hasDraft;
  const selectedReasoningLabel = composerOptionLabel(reasoningOptions, reasoningEffort) || reasoningEffort || 'Auto';
  const fileDropEnabled = typeof onFilesSelected === 'function' && !controlsDisabled;
  const menuCapabilityItems = useMemo<readonly ComposerCapabilityItem[]>(() => {
    if (!fileDropEnabled || capabilityItems.some((item) => item.id === 'files')) {
      return capabilityItems;
    }
    return [
      {
        id: 'files',
        title: 'Add files',
        icon: Paperclip,
        onSelect: () => {},
      },
      ...capabilityItems,
    ];
  }, [capabilityItems, fileDropEnabled]);
  const availableReasoningOptions = useMemo(
    () => reasoningOptions.filter((option) => !option.disabled),
    [reasoningOptions],
  );
  const showInlineReasoningToggle = false;
  const voiceSupported = typeof onVoiceTranscribe === 'function'
    && typeof window !== 'undefined'
    && typeof navigator !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof MediaRecorder !== 'undefined';

  const commandQuery = useMemo(() => {
    if (!draft.startsWith('/') || draft.includes('\n')) {
      return null;
    }
    const normalized = draft.slice(1).trimStart();
    if (!normalized) {
      return '';
    }
    if (/\s/.test(normalized)) {
      return null;
    }
    return normalized.toLowerCase();
  }, [draft]);
  const filteredSlashCommands = useMemo(() => {
    if (commandQuery === null) {
      return [];
    }
    if (!commandQuery) {
      return [...slashCommands];
    }
    return slashCommands.filter((command) => {
      const haystack = [
        command.slash,
        command.title,
        command.description,
        ...(command.keywords ?? []),
      ].join(' ').toLowerCase();
      return haystack.includes(commandQuery);
    });
  }, [commandQuery, slashCommands]);
  const commandPaletteVisible = !commandPaletteDismissed
    && commandQuery !== null
    && filteredSlashCommands.length > 0
    && typeof onSlashCommandSelect === 'function';
  const actionMenuVisible = actionPaletteOpen
    && menuCapabilityItems.length > 0;
  const [activeCapabilitySubmenuId, setActiveCapabilitySubmenuId] = useState<string | null>(null);
  const activeCapabilitySubmenu = useMemo(
    () => menuCapabilityItems.find((item) => item.id === activeCapabilitySubmenuId && item.submenuItems && item.submenuItems.length > 0) ?? null,
    [activeCapabilitySubmenuId, menuCapabilityItems],
  );

  const submitDraft = () => {
    if (!canSend) {
      return;
    }
    onSubmit();
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitDraft();
  };

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) {
      return;
    }
    element.style.height = '0px';
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
    element.style.overflowY = element.scrollHeight > 200 ? 'auto' : 'hidden';
  }, [draft]);

  useEffect(() => {
    textareaRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    setSelectedCommandIndex(0);
  }, [actionPaletteOpen, commandQuery]);

  useEffect(() => {
    if (!actionMenuVisible) {
      setActiveCapabilitySubmenuId(null);
      return;
    }
    setActiveCapabilitySubmenuId((current) => {
      if (current && menuCapabilityItems.some((item) => item.id === current && item.submenuItems && item.submenuItems.length > 0)) {
        return current;
      }
      return null;
    });
  }, [actionMenuVisible, menuCapabilityItems]);

  useEffect(() => {
    if (selectedCommandIndex < filteredSlashCommands.length) {
      return;
    }
    setSelectedCommandIndex(Math.max(0, filteredSlashCommands.length - 1));
  }, [filteredSlashCommands.length, selectedCommandIndex]);

  useEffect(() => {
    if (!actionPaletteOpen && !commandPaletteVisible) {
      return undefined;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (
        actionLauncherRef.current?.contains(target)
        || commandPaletteRef.current?.contains(target)
      ) {
        return;
      }
      setActionPaletteOpen(false);
      setCommandPaletteDismissed(true);
    };
    window.addEventListener('pointerdown', handlePointerDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [actionPaletteOpen, commandPaletteVisible]);

  const selectSlashCommand = (command: ComposerSlashCommand) => {
    setCommandPaletteDismissed(true);
    setActionPaletteOpen(false);
    onSlashCommandSelect?.(command);
  };

  const stopVoiceRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      return;
    }
    recorder.stop();
  };

  const startVoiceRecording = async () => {
    if (!voiceSupported) {
      setVoiceNotice('Voice is unavailable in this browser.');
      return;
    }
    setVoiceNotice(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordedChunksRef.current = [];
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const audio = new Blob(recordedChunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        });
        recordedChunksRef.current = [];
        mediaRecorderRef.current = null;
        if (!audio.size || !onVoiceTranscribe) {
          setVoiceState('idle');
          return;
        }
        setVoiceState('transcribing');
        void onVoiceTranscribe(audio)
          .then((transcript) => {
            const cleanTranscript = transcript.trim();
            if (!cleanTranscript) {
              setVoiceNotice('No speech detected.');
              return;
            }
            const currentDraft = draftRef.current.trim();
            onDraftChange(currentDraft ? `${currentDraft}\n${cleanTranscript}` : cleanTranscript);
            setVoiceNotice('Voice added. Press send when ready.');
          })
          .catch((error) => {
            setVoiceNotice(error instanceof Error ? error.message : 'Voice could not be transcribed.');
          })
          .finally(() => {
            setVoiceState('idle');
          });
      };
      recorder.start();
      setVoiceState('recording');
    } catch {
      setVoiceNotice('Microphone access was blocked.');
      setVoiceState('idle');
    }
  };

  const handleVoiceButton = () => {
    if (voiceState === 'recording') {
      stopVoiceRecording();
      return;
    }
    if (voiceState === 'transcribing' || busy) {
      return;
    }
    void startVoiceRecording();
  };

  const handleDraftChange = (nextDraft: string) => {
    if (!nextDraft.startsWith('/')) {
      setCommandPaletteDismissed(true);
      setActionPaletteOpen(false);
    } else {
      setActionPaletteOpen(false);
      setCommandPaletteDismissed(false);
    }
    onDraftChange(nextDraft);
  };

  const cycleReasoningEffort = () => {
    if (availableReasoningOptions.length <= 1) {
      return;
    }
    const currentIndex = availableReasoningOptions.findIndex((option) => option.value === reasoningEffort);
    const nextIndex = currentIndex >= 0
      ? (currentIndex + 1) % availableReasoningOptions.length
      : 0;
    const nextOption = availableReasoningOptions[nextIndex];
    if (nextOption) {
      onReasoningEffortChange(nextOption.value);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (commandPaletteVisible) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setSelectedCommandIndex((current) => (current + 1) % filteredSlashCommands.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setSelectedCommandIndex((current) => (
          current === 0 ? filteredSlashCommands.length - 1 : current - 1
        ));
        return;
      }
      if (event.key === 'Tab' || event.key === 'Enter') {
        const selectedCommand = filteredSlashCommands[selectedCommandIndex];
        if (selectedCommand) {
          event.preventDefault();
          selectSlashCommand(selectedCommand);
          return;
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setActionPaletteOpen(false);
        setCommandPaletteDismissed(true);
        return;
      }
    }
    if (event.key !== 'Enter') {
      if (event.key === 'Escape' && canStop) {
        event.preventDefault();
        onStop?.();
      }
      return;
    }
    if (event.shiftKey) {
      return;
    }
    event.preventDefault();
    submitDraft();
  };
  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);
  const eventHasFiles = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
  const handleFilesSelected = (files: FileList | null) => {
    const incomingFiles = Array.from(files ?? []);
    if (incomingFiles.length === 0) {
      return;
    }
    void onFilesSelected?.(incomingFiles);
  };
  const handleFileDragEnter = (event: DragEvent<HTMLFormElement>) => {
    if (!fileDropEnabled || !eventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    fileDragDepthRef.current += 1;
    setFileDragActive(true);
  };
  const handleFileDragOver = (event: DragEvent<HTMLFormElement>) => {
    if (!fileDropEnabled || !eventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  };
  const handleFileDragLeave = (event: DragEvent<HTMLFormElement>) => {
    if (!fileDropEnabled || !eventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    fileDragDepthRef.current = Math.max(0, fileDragDepthRef.current - 1);
    if (fileDragDepthRef.current === 0) {
      setFileDragActive(false);
    }
  };
  const handleFileDrop = (event: DragEvent<HTMLFormElement>) => {
    if (!fileDropEnabled || !eventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    fileDragDepthRef.current = 0;
    setFileDragActive(false);
    handleFilesSelected(event.dataTransfer.files);
  };
  return (
    <section data-workstation-chat-composer="root" className="app-chat-composer">
      <form
        className={joinClassNames(
          'app-chat-composer__surface',
          fileDragActive && 'app-chat-composer__surface--file-dragging',
          showInlineReasoningToggle && 'app-chat-composer__surface--reasoning-visible',
        )}
        onSubmit={handleSubmit}
        onDragEnter={handleFileDragEnter}
        onDragOver={handleFileDragOver}
        onDragLeave={handleFileDragLeave}
        onDrop={handleFileDrop}
      >
        {attachments.length > 0 ? (
          <div className="app-chat-composer__attachments">
            {attachments.map((attachment, index) => (
              <span key={`${attachment.file_id}-${index}`} className="app-chat-composer__attachment-chip">
                <Paperclip size={12} strokeWidth={2} aria-hidden="true" />
                <span>{attachment.filename}</span>
                <button
                  type="button"
                  className="app-chat-composer__attachment-remove"
                  aria-label={`Remove ${attachment.filename}`}
                  onClick={() => onRemoveAttachment?.(attachment)}
                >
                  <X size={12} strokeWidth={2} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(event) => handleDraftChange(event.currentTarget.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={placeholder}
          className="app-textarea app-chat-composer__textarea"
          disabled={sendDisabled && !busy}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="app-chat-composer__file-input"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            handleFilesSelected(event.currentTarget.files);
            event.currentTarget.value = '';
          }}
        />

        {fileDragActive ? (
          <div className="app-chat-composer__drop-overlay" aria-hidden="true">
            <Paperclip size={16} strokeWidth={2} />
            <span>Drop files</span>
          </div>
        ) : null}

        {smallModelWarning ? (
          <div className="app-chat-composer__small-model-warning" role="status">
            <span>{smallModelWarning}</span>
            <button
              type="button"
              className="app-chat-composer__small-model-warning-dismiss"
              aria-label="Dismiss small model warning"
              onClick={onDismissSmallModelWarning}
            >
              <X size={13} strokeWidth={2} aria-hidden="true" />
            </button>
          </div>
        ) : null}

        {showInlineReasoningToggle ? (
          <button
            type="button"
            className="app-chat-composer__inline-reasoning"
            disabled={controlsDisabled || busy}
            aria-label={`Reasoning mode: ${selectedReasoningLabel}. Click to change.`}
            onClick={cycleReasoningEffort}
          >
            <Brain size={13} strokeWidth={2} aria-hidden="true" />
            <span>{selectedReasoningLabel}</span>
          </button>
        ) : null}

        <div className="app-chat-composer__toolbar">
          <div
            className={joinClassNames(
                  'app-chat-composer__toolbar-left',
              (actionMenuVisible || commandPaletteVisible || modelControlOpen)
                && 'app-chat-composer__toolbar-left--menu-open',
            )}
          >
            <div className="app-chat-composer__actions" ref={actionLauncherRef}>
              <button
                type="button"
                className={joinClassNames(
                  'app-chat-composer__action-trigger',
                  'app-chat-composer__icon-trigger',
                )}
                onClick={() => {
                  onComposerMenuOpen?.();
                  setCommandPaletteDismissed(true);
                  setActionPaletteOpen((current) => !current);
                }}
                aria-expanded={actionMenuVisible}
                aria-label="Add actions"
              >
                <Plus size={18} strokeWidth={2.1} aria-hidden="true" />
              </button>

              {actionMenuVisible ? (
                <div
                  className="app-chat-composer__action-menu app-chat-composer__action-menu--capabilities"
                  role="dialog"
                  aria-label="Add actions"
                >
                  <div className="app-chat-composer__capability-list" role="list">
                    {menuCapabilityItems.map((item) => {
                      const CapabilityIcon = item.icon ?? capabilityOptionIcon(item.id);
                      const hasSubmenu = Boolean(item.submenuItems && item.submenuItems.length > 0);
                      const isSubmenuActive = activeCapabilitySubmenuId === item.id;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={joinClassNames(
                            'app-chat-composer__capability-row',
                            hasSubmenu && 'app-chat-composer__capability-row--has-submenu',
                            isSubmenuActive && 'app-chat-composer__capability-row--active',
                          )}
                          onPointerEnter={() => {
                            if (hasSubmenu) {
                              setActiveCapabilitySubmenuId(item.id);
                            }
                          }}
                          onPointerMove={() => {
                            if (hasSubmenu) {
                              setActiveCapabilitySubmenuId(item.id);
                            }
                          }}
                          onMouseEnter={() => {
                            if (hasSubmenu) {
                              setActiveCapabilitySubmenuId(item.id);
                            }
                          }}
                          onFocus={() => {
                            if (hasSubmenu) {
                              setActiveCapabilitySubmenuId(item.id);
                            }
                          }}
                          onClick={() => {
                            if (hasSubmenu) {
                              setActiveCapabilitySubmenuId(item.id);
                              return;
                            }
                            setActionPaletteOpen(false);
                            if (item.id === 'files') {
                              openFilePicker();
                              return;
                            }
                            item.onSelect();
                          }}
                        >
                          <span className="app-chat-composer__capability-icon" aria-hidden="true">
                            <CapabilityIconMark icon={CapabilityIcon} iconSrc={item.iconSrc} />
                          </span>
                          <strong>{item.title}</strong>
                          {hasSubmenu ? (
                            <ChevronRight className="app-chat-composer__capability-chevron" size={16} strokeWidth={1.9} aria-hidden="true" />
                          ) : item.status ? (
                            <span
                              className={joinClassNames(
                                'app-chat-composer__capability-status',
                                item.statusTone && `app-chat-composer__capability-status--${item.statusTone}`,
                              )}
                            >
                              {item.status}
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                  {activeCapabilitySubmenu ? (
                    <div
                      className="app-chat-composer__capability-subpanel"
                      role="menu"
                      aria-label={activeCapabilitySubmenu.submenuTitle ?? activeCapabilitySubmenu.title}
                    >
                      <div className="app-chat-composer__capability-subpanel-title">
                        {activeCapabilitySubmenu.submenuTitle ?? activeCapabilitySubmenu.title}
                      </div>
                      <div className="app-chat-composer__capability-subpanel-list" role="list">
                        {(activeCapabilitySubmenu.submenuItems ?? []).map((subItem) => {
                          const SubItemIcon = subItem.icon ?? capabilityOptionIcon(subItem.id);
                          const isToggle = subItem.itemType === 'toggle';
                          return (
                            <button
                              key={subItem.id}
                              type="button"
                              className={joinClassNames(
                                'app-chat-composer__capability-subrow',
                                subItem.dividerBefore && 'app-chat-composer__capability-subrow--divider-before',
                              )}
                              onClick={() => {
                                setActionPaletteOpen(false);
                                subItem.onSelect?.();
                              }}
                            >
                              <span className="app-chat-composer__capability-icon" aria-hidden="true">
                                <CapabilityIconMark icon={SubItemIcon} iconSrc={subItem.iconSrc} />
                              </span>
                              <span className="app-chat-composer__capability-subrow-copy">
                                <strong>{subItem.title}</strong>
                                {subItem.detail ? <small>{subItem.detail}</small> : null}
                              </span>
                              {isToggle ? (
                                <span
                                  className={joinClassNames(
                                    'app-chat-composer__capability-switch',
                                    subItem.enabled && 'app-chat-composer__capability-switch--on',
                                  )}
                                  role="switch"
                                  aria-checked={subItem.enabled === true}
                                  aria-label={`${subItem.title} connector`}
                                >
                                  <span />
                                </span>
                              ) : subItem.status ? (
                                <span
                                  className={joinClassNames(
                                    'app-chat-composer__capability-status',
                                    subItem.statusTone && `app-chat-composer__capability-status--${subItem.statusTone}`,
                                  )}
                                >
                                  {subItem.status}
                                </span>
                              ) : null}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {commandPaletteVisible ? (
                <div
                  ref={commandPaletteRef}
                  className="app-chat-composer__command-palette"
                  role="dialog"
                  aria-label="Sage actions"
                >
                  <div className="app-chat-composer__command-list" role="listbox" aria-label="Available Sage actions">
                    {filteredSlashCommands.map((command, index) => (
                      <button
                        key={command.id}
                        type="button"
                        className={joinClassNames(
                          'app-chat-composer__command-item',
                          index === selectedCommandIndex && 'app-chat-composer__command-item--active',
                        )}
                        onMouseEnter={() => setSelectedCommandIndex(index)}
                        onClick={() => selectSlashCommand(command)}
                        role="option"
                        aria-selected={index === selectedCommandIndex}
                      >
                        <command.icon size={16} strokeWidth={1.9} aria-hidden="true" />
                        <span className="app-chat-composer__command-copy">
                          <span className="app-chat-composer__command-title-row">
                            <strong>{command.title}</strong>
                            <span className="app-chat-composer__command-description">{command.description}</span>
                          </span>
                        </span>
                        <span className="app-chat-composer__command-shortcut">{command.slash}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
            {modelControl}
          </div>

          <button
            type="button"
            className={joinClassNames(
              'app-chat-composer__voice',
              voiceState === 'recording' && 'app-chat-composer__voice--recording',
            )}
            disabled={busy || voiceState === 'transcribing'}
            aria-label={voiceState === 'recording' ? 'Stop voice recording' : 'Record voice'}
            onClick={handleVoiceButton}
          >
            {voiceState === 'recording' ? (
              <MicOff size={17} strokeWidth={2.1} aria-hidden="true" />
            ) : (
              <Mic size={17} strokeWidth={2.1} aria-hidden="true" />
            )}
          </button>

          <AppButton
            type={busy ? 'button' : 'submit'}
            onClick={busy ? onStop : undefined}
            disabled={busy ? !canStop : !canSend}
            aria-label={sendDisabled && !busy ? 'Send message and review provider setup' : busy ? 'Stop response' : 'Send message'}
            className={joinClassNames(
              'app-chat-composer__send',
              busy && 'app-chat-composer__send--stop',
              !showSendButton && 'app-chat-composer__send--hidden',
            )}
          >
            {busy ? (
              <Square size={14} strokeWidth={2.6} aria-hidden="true" fill="currentColor" />
            ) : (
              <ArrowUp size={16} strokeWidth={2.1} aria-hidden="true" />
            )}
          </AppButton>
        </div>
      </form>

      {voiceNotice ? (
        <PlatformNotification
          tone={voiceNotice.toLowerCase().includes('blocked') || voiceNotice.toLowerCase().includes('could not') ? 'warning' : 'success'}
          title="Voice input"
          detail={voiceNotice}
          onClose={() => setVoiceNotice(null)}
        />
      ) : null}
    </section>
  );
}
